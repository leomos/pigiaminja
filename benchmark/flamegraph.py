#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "psycopg[binary]>=3.1",
# ]
# ///
"""
Flamegraph profiler for pigiaminja.

Profiles the PostgreSQL backend while running a pigiaminja COPY TO query
and generates a flamegraph.

Sampling backends (in auto-detect priority order):
  - samply  (best: opens Firefox Profiler UI, install: cargo install --locked samply)
  - perf    (generates SVG via inferno, requires linux-tools matching your kernel)
  - gdb     (fallback, lower precision but works everywhere, SVG via inferno)

Prerequisites:
  1. PostgreSQL running with pigiaminja loaded and a bench_data table
     (run bench.py first to create it, or use --setup-table)
  2. Build pigiaminja with debug symbols for meaningful stack frames:
       CARGO_PROFILE_RELEASE_DEBUG=2 cargo pgrx install --release ...
  3. Install a profiling backend:
       cargo install --locked samply    # recommended
       # or: ensure perf + cargo install inferno
       # or: ensure gdb + cargo install inferno

Usage:
  uv run benchmark/flamegraph.py --setup-table
  uv run benchmark/flamegraph.py --backend samply --rows 500000
  uv run benchmark/flamegraph.py --backend perf -o profile.svg
"""

import argparse
import collections
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psycopg


# --- Constants ---

if sys.platform == "darwin":
    DSN = "host=localhost port=28818 dbname=postgres"
else:
    DSN = "host=/var/run/postgresql dbname=postgres user=postgres password=benchpass"
COLUMNS = "id, name, email, department, salary, is_active, score, created_at, notes"
JINJA_TEMPLATE = (
    "{{ row.id }},{{ row.name }},{{ row.email }},{{ row.department }},"
    "{{ row.salary }},{{ row.is_active }},{{ row.score }},"
    "{{ row.created_at }},{{ row.notes }}"
)


# --- Table setup ---

def setup_table(conn, row_count):
    """Create and populate bench_data if --setup-table is given."""
    print(f"  Creating bench_data with {row_count:,} rows...", end=" ", flush=True)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench_data")
        cur.execute("""
            CREATE TABLE bench_data (
                id INTEGER, name TEXT, email TEXT, department VARCHAR(50),
                salary NUMERIC(10,2), is_active BOOLEAN, score DOUBLE PRECISION,
                created_at TIMESTAMP, notes TEXT
            )
        """)
        cur.execute("""
            INSERT INTO bench_data SELECT i, 'user_' || i,
                'user_' || i || '@example.com',
                (ARRAY['Engineering','Marketing','Sales','HR','Finance'])[1 + (i %% 5)],
                30000 + (i %% 70000)::numeric + 0.50, (i %% 3 != 0),
                (i %% 1000)::double precision / 7.0,
                '2020-01-01'::timestamp + (i || ' seconds')::interval,
                'Notes for employee number ' || i
            FROM generate_series(1, %(n)s) AS s(i)
        """, {"n": row_count})
        cur.execute("ANALYZE bench_data")
    conn.commit()
    print("done.")


# --- Query runner ---

def run_query(conn):
    """Run the pigiaminja COPY TO query. Returns byte count."""
    copy_sql = (
        f"COPY (SELECT {COLUMNS} FROM bench_data) "
        f"TO STDOUT (FORMAT 'jinja', TEMPLATE '{JINJA_TEMPLATE}')"
    )
    total = 0
    with conn.cursor().copy(copy_sql) as copy:
        for chunk in copy:
            total += len(bytes(chunk) if isinstance(chunk, memoryview) else chunk)
    return total


# --- perf backend ---

def check_perf():
    """Check if perf is usable (not just installed, but actually works)."""
    try:
        r = subprocess.run(
            ["perf", "stat", "--", "true"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def profile_with_perf(pid, conn, freq):
    """Profile using perf record, return path to collapsed stacks file."""
    perf_data = tempfile.mktemp(suffix=".perf.data")
    collapsed = tempfile.mktemp(suffix=".collapsed")

    # Start perf record in background
    perf_proc = subprocess.Popen(
        ["perf", "record", "-g", "--call-graph", "dwarf,16384",
         "-F", str(freq), "-p", str(pid), "-o", perf_data],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    # Give perf a moment to attach
    time.sleep(0.2)

    # Run the query
    t0 = time.perf_counter()
    total_bytes = run_query(conn)
    elapsed = time.perf_counter() - t0
    print(f"  Query completed in {elapsed:.3f}s, {total_bytes:,} bytes")

    # Stop perf
    perf_proc.terminate()
    perf_proc.wait(timeout=10)

    # Convert perf data to collapsed stacks
    inferno_collapse = shutil.which("inferno-collapse-perf")
    if inferno_collapse:
        perf_script = subprocess.run(
            ["perf", "script", "-i", perf_data],
            capture_output=True,
        )
        collapse_result = subprocess.run(
            [inferno_collapse],
            input=perf_script.stdout, capture_output=True,
        )
        with open(collapsed, "wb") as f:
            f.write(collapse_result.stdout)
    else:
        # Fall back to perf script + manual folding
        perf_script = subprocess.run(
            ["perf", "script", "-i", perf_data],
            capture_output=True, text=True,
        )
        stacks = fold_perf_script(perf_script.stdout)
        with open(collapsed, "w") as f:
            for stack, count in stacks.items():
                f.write(f"{stack} {count}\n")

    os.unlink(perf_data)
    return collapsed


def fold_perf_script(text):
    """Minimally fold perf script output into collapsed stacks."""
    stacks = collections.Counter()
    current_frames = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            if current_frames:
                current_frames.reverse()
                stacks[";".join(current_frames)] += 1
                current_frames = []
        elif line.startswith("\t"):
            # Stack frame line: \taddr func+off (lib)
            parts = line.strip().split()
            if len(parts) >= 2:
                func = parts[1].split("+")[0]
                current_frames.append(func)
    if current_frames:
        current_frames.reverse()
        stacks[";".join(current_frames)] += 1
    return stacks


# --- samply backend ---

def check_samply():
    """Check if samply is installed."""
    return shutil.which("samply") is not None


def profile_with_samply(pid, conn, freq, output):
    """Profile using samply record -p PID, writes a Firefox Profiler json.gz."""
    # samply outputs profile.json.gz by default
    samply_output = output if output.endswith(".json.gz") else output + ".json.gz"

    samply_proc = subprocess.Popen(
        ["samply", "record", "-p", str(pid),
         "--rate", str(freq), "--save-only", "-o", samply_output],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    # Give samply a moment to attach
    time.sleep(0.3)

    # Run the query
    t0 = time.perf_counter()
    total_bytes = run_query(conn)
    elapsed = time.perf_counter() - t0
    print(f"  Query completed in {elapsed:.3f}s, {total_bytes:,} bytes")

    # Stop samply gracefully with SIGINT (it needs this to finalize the profile)
    samply_proc.send_signal(signal.SIGINT)
    try:
        samply_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        samply_proc.kill()

    if not os.path.exists(samply_output):
        stderr = samply_proc.stderr.read().decode() if samply_proc.stderr else ""
        print(f"  ERROR: samply did not produce output file: {samply_output}")
        if stderr:
            print(f"  stderr: {stderr}")
        sys.exit(1)

    return samply_output


# --- GDB backend ---

def get_stack_gdb(pid):
    """Capture one stack sample via GDB."""
    try:
        r = subprocess.run(
            ["gdb", "-batch", "-ex", "thread apply all bt", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def parse_gdb_stack(raw):
    """Parse GDB backtrace into collapsed frame list (root first)."""
    frames = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("#"):
            continue
        parts = line.split(" in ", 1)
        if len(parts) >= 2:
            func = parts[1].split(" (")[0].split(" at ")[0].strip()
            frames.append(func)
        else:
            tokens = line.split()
            if len(tokens) >= 4:
                frames.append(tokens[3])
    frames.reverse()
    return frames


def gdb_sample_loop(pid, stacks, stop_event, interval):
    """Continuously sample via GDB until stop_event is set."""
    while not stop_event.is_set():
        raw = get_stack_gdb(pid)
        if raw:
            frames = parse_gdb_stack(raw)
            if frames:
                stacks[";".join(frames)] += 1
        time.sleep(interval)


def profile_with_gdb(pid, conn, interval=0.01):
    """Profile using GDB sampling, return path to collapsed stacks file."""
    collapsed = tempfile.mktemp(suffix=".collapsed")

    stacks = collections.Counter()
    stop_event = threading.Event()
    sampler = threading.Thread(
        target=gdb_sample_loop,
        args=(pid, stacks, stop_event, interval),
    )
    sampler.daemon = True
    sampler.start()

    t0 = time.perf_counter()
    total_bytes = run_query(conn)
    elapsed = time.perf_counter() - t0

    stop_event.set()
    sampler.join(timeout=5)

    total_samples = sum(stacks.values())
    print(f"  Query completed in {elapsed:.3f}s, {total_bytes:,} bytes")
    print(f"  Collected {total_samples} stack samples")

    if total_samples == 0:
        print("  ERROR: No samples collected. Is GDB installed? Can it attach to postgres?")
        sys.exit(1)

    with open(collapsed, "w") as f:
        for stack, count in stacks.most_common():
            f.write(f"{stack} {count}\n")

    return collapsed


# --- Flamegraph rendering ---

def render_flamegraph(collapsed_path, output_svg, title="pigiaminja profile"):
    """Convert collapsed stacks to SVG using inferno-flamegraph."""
    inferno = shutil.which("inferno-flamegraph")
    if not inferno:
        print(f"\n  Collapsed stacks saved to: {collapsed_path}")
        print("  To generate an SVG flamegraph, install inferno:")
        print("    cargo install inferno")
        print(f"  Then run:")
        print(f"    inferno-flamegraph --title '{title}' < {collapsed_path} > {output_svg}")
        return False

    with open(collapsed_path) as f:
        result = subprocess.run(
            [inferno, "--title", title],
            stdin=f, capture_output=True,
        )

    if result.returncode != 0:
        print(f"  inferno-flamegraph failed: {result.stderr.decode()}")
        return False

    with open(output_svg, "wb") as f:
        f.write(result.stdout)

    return True


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Generate a flamegraph for pigiaminja COPY TO queries",
    )
    parser.add_argument(
        "--backend", choices=["samply", "perf", "gdb", "auto"], default="auto",
        help="Sampling backend (default: auto-detect, prefers samply > perf > gdb)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output path (default: profile.json.gz for samply, flamegraph.svg for perf/gdb)",
    )
    parser.add_argument(
        "--rows", type=int, default=100_000,
        help="Number of rows in bench_data table (default: 100000)",
    )
    parser.add_argument(
        "--freq", type=int, default=997,
        help="perf sampling frequency in Hz (default: 997)",
    )
    parser.add_argument(
        "--setup-table", action="store_true",
        help="Create/recreate bench_data table before profiling",
    )
    parser.add_argument("--dsn", default=DSN, help="PostgreSQL connection string")
    args = parser.parse_args()

    print("=== pigiaminja flamegraph profiler ===\n")

    # Pick backend
    if args.backend == "auto":
        if check_samply():
            backend = "samply"
        elif check_perf():
            backend = "perf"
        elif shutil.which("gdb"):
            backend = "gdb"
        else:
            print("ERROR: No profiling backend found. Install one of:")
            print("  cargo install --locked samply   (recommended)")
            print("  apt install linux-tools-$(uname -r)   (perf)")
            print("  apt install gdb   (fallback)")
            sys.exit(1)
    else:
        backend = args.backend

    # Set default output based on backend
    if args.output is None:
        output = "profile.json.gz" if backend == "samply" else "flamegraph.svg"
    else:
        output = args.output

    print(f"  Backend: {backend}")
    print(f"  Output:  {output}")

    # Connect
    conn = psycopg.connect(args.dsn, autocommit=True)
    pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
    print(f"  PG PID:  {pid}")

    if args.setup_table:
        setup_table(conn, args.rows)

    # Verify table exists
    count = conn.execute("SELECT count(*) FROM bench_data").fetchone()[0]
    print(f"  Rows:    {count:,}\n")

    # Warmup
    print("  Warming up...", end=" ", flush=True)
    run_query(conn)
    print("done.")

    # Profile
    print(f"  Profiling with {backend}...")
    if backend == "samply":
        samply_output = profile_with_samply(pid, conn, args.freq, output)
        size = os.path.getsize(samply_output)
        print(f"\n  Profile written to: {samply_output} ({size:,} bytes)")
        print(f"  View with: samply load {samply_output}")
    elif backend == "perf":
        collapsed = profile_with_perf(pid, conn, args.freq)
        print(f"\n  Generating flamegraph...")
        title = f"pigiaminja COPY TO ({count:,} rows, {backend})"
        ok = render_flamegraph(collapsed, output, title)
        if ok:
            print(f"\n  Flamegraph written to: {output}")
        else:
            print(f"\n  Collapsed stacks at: {collapsed}")
            print(f"  Install inferno to render: cargo install inferno")
    else:
        collapsed = profile_with_gdb(pid, conn)
        print(f"\n  Generating flamegraph...")
        title = f"pigiaminja COPY TO ({count:,} rows, {backend})"
        ok = render_flamegraph(collapsed, output, title)
        if ok:
            print(f"\n  Flamegraph written to: {output}")
        else:
            print(f"\n  Collapsed stacks at: {collapsed}")
            print(f"  Install inferno to render: cargo install inferno")

    conn.close()


if __name__ == "__main__":
    main()
