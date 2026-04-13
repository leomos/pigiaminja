#!/usr/bin/env python3
"""GDB-based sampling profiler for PostgreSQL backend processes.
Samples userspace stacks and produces a collapsed stack format suitable for flamegraph.pl."""

import subprocess
import sys
import time
import threading
import collections
import os
import signal

import psycopg


DSN = "host=/var/run/postgresql dbname=postgres user=postgres password=benchpass"
SAMPLE_INTERVAL = 0.005  # 5ms = ~200Hz sampling rate
COLUMNS = "id, name, email, department, salary, is_active, score, created_at, notes"
JINJA_TEMPLATE = (
    "{{ row.id }},{{ row.name }},{{ row.email }},{{ row.department }},"
    "{{ row.salary }},{{ row.is_active }},{{ row.score }},"
    "{{ row.created_at }},{{ row.notes }}"
)


def get_stack_gdb(pid):
    """Get a stack trace from a process using gdb."""
    try:
        result = subprocess.run(
            ["gdb", "-batch", "-ex", "thread apply all bt", "-p", str(pid)],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout
    except (subprocess.TimeoutExpired, Exception):
        return ""


def parse_gdb_stack(raw):
    """Parse gdb backtrace into a list of frames (bottom to top)."""
    frames = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#"):
            # Format: #N  0xADDR in func_name (args) at file:line
            parts = line.split(" in ", 1)
            if len(parts) >= 2:
                func_part = parts[1].split(" (")[0].split(" at ")[0].strip()
                frames.append(func_part)
            else:
                # Format: #N  0xADDR in func_name ()
                parts2 = line.split()
                if len(parts2) >= 4:
                    frames.append(parts2[3])
    # Reverse so it's bottom-up (root first)
    frames.reverse()
    return frames


def sample_loop(pid, stacks, stop_event):
    """Continuously sample the target process."""
    while not stop_event.is_set():
        raw = get_stack_gdb(pid)
        if raw:
            frames = parse_gdb_stack(raw)
            if frames:
                key = ";".join(frames)
                stacks[key] += 1
        time.sleep(SAMPLE_INTERVAL)


def run_query(conn, query_type="jinja"):
    """Run the benchmark query. Returns when complete."""
    if query_type == "jinja":
        copy_sql = (
            f"COPY (SELECT {COLUMNS} FROM bench_data) "
            f"TO STDOUT (FORMAT 'jinja', TEMPLATE '{JINJA_TEMPLATE}')"
        )
    else:
        copy_sql = f"COPY (SELECT {COLUMNS} FROM bench_data) TO STDOUT WITH (FORMAT CSV)"

    total = 0
    with conn.cursor().copy(copy_sql) as copy:
        for chunk in copy:
            total += len(bytes(chunk) if isinstance(chunk, memoryview) else chunk)
    return total


def main():
    print("=== GDB-based Profiler for pigiaminja ===\n")

    # Connect and get backend PID
    conn = psycopg.connect(DSN, autocommit=True)
    pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
    print(f"PostgreSQL backend PID: {pid}")

    # Warm up
    print("Warming up...", flush=True)
    run_query(conn, "jinja")
    print("Warmup done.\n")

    # Profile the jinja query
    print(f"Profiling pigiaminja COPY jinja (100k rows)...")
    print(f"Sampling at ~{1/SAMPLE_INTERVAL:.0f}Hz...\n")

    stacks = collections.Counter()
    stop_event = threading.Event()

    sampler = threading.Thread(target=sample_loop, args=(pid, stacks, stop_event))
    sampler.daemon = True
    sampler.start()

    t0 = time.perf_counter()
    total_bytes = run_query(conn, "jinja")
    elapsed = time.perf_counter() - t0

    stop_event.set()
    sampler.join(timeout=3)

    total_samples = sum(stacks.values())
    print(f"Query completed in {elapsed:.3f}s, {total_bytes:,} bytes")
    print(f"Collected {total_samples} stack samples\n")

    if total_samples == 0:
        print("ERROR: No samples collected. GDB may not have had permission.")
        sys.exit(1)

    # Write collapsed stack format for flame graph generation
    collapsed_file = "/tmp/pigiaminja_stacks.collapsed"
    with open(collapsed_file, "w") as f:
        for stack, count in stacks.most_common():
            f.write(f"{stack} {count}\n")
    print(f"Collapsed stacks written to: {collapsed_file}")

    # Print top functions
    func_counts = collections.Counter()
    for stack, count in stacks.items():
        for frame in stack.split(";"):
            func_counts[frame] += count

    print(f"\n{'='*70}")
    print(f"  Top functions by sample count (total: {total_samples} samples)")
    print(f"{'='*70}\n")
    print(f"  {'Samples':>8} {'%':>6}  Function")
    print(f"  {'-'*8} {'-'*6}  {'-'*50}")
    for func, count in func_counts.most_common(30):
        pct = 100.0 * count / total_samples
        print(f"  {count:8d} {pct:5.1f}%  {func}")

    # Also print top stacks
    print(f"\n{'='*70}")
    print(f"  Top 10 stack traces")
    print(f"{'='*70}\n")
    for stack, count in stacks.most_common(10):
        pct = 100.0 * count / total_samples
        print(f"  [{count} samples, {pct:.1f}%]")
        for frame in stack.split(";"):
            print(f"    {frame}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
