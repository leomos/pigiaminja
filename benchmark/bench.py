#!/usr/bin/env python3
"""
Benchmark: pigiaminja (Jinja COPY TO) vs native COPY CSV vs psycopg client-side CSV

Compares three approaches for exporting PostgreSQL data:
  A) pigiaminja COPY TO with FORMAT 'jinja' (server-side Jinja rendering)
  B) Native COPY TO with FORMAT CSV (PostgreSQL built-in, baseline)
  C) psycopg client-side: SELECT + Python string formatting
"""

import argparse
import gc
import io
import json
import statistics
import sys
import time

import psycopg


# --- Constants ---

DSN = "host=/var/run/postgresql dbname=postgres user=postgres password=benchpass"

ROW_COUNTS = [10_000, 100_000, 1_000_000]
WARMUP_ITERATIONS = 2
BENCH_ITERATIONS = 5

COLUMNS = "id, name, email, department, salary, is_active, score, created_at, notes"

JINJA_TEMPLATE = (
    "{{ row.id }},{{ row.name }},{{ row.email }},{{ row.department }},"
    "{{ row.salary }},{{ row.is_active }},{{ row.score }},"
    "{{ row.created_at }},{{ row.notes }}"
)


# --- Helpers ---

class ByteCounter:
    """Counts bytes and lines without storing data."""

    def __init__(self):
        self.byte_count = 0
        self.line_count = 0

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        elif isinstance(data, memoryview):
            data = bytes(data)
        self.byte_count += len(data)
        self.line_count += data.count(b"\n")


class BenchResult:
    def __init__(self, approach, row_count, times, byte_count, line_count):
        self.approach = approach
        self.row_count = row_count
        self.times = times
        self.byte_count = byte_count
        self.line_count = line_count

    @property
    def mean(self):
        return statistics.mean(self.times)

    @property
    def median(self):
        return statistics.median(self.times)

    @property
    def stdev(self):
        return statistics.stdev(self.times) if len(self.times) > 1 else 0.0

    @property
    def throughput(self):
        return self.row_count / self.mean if self.mean > 0 else 0


# --- Database setup ---

def setup_schema(conn, row_count):
    """Create and populate the bench_data table."""
    print(f"  Setting up {row_count:,} rows...", end=" ", flush=True)
    t0 = time.perf_counter()

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench_data")
        cur.execute("""
            CREATE TABLE bench_data (
                id          INTEGER,
                name        TEXT,
                email       TEXT,
                department  VARCHAR(50),
                salary      NUMERIC(10,2),
                is_active   BOOLEAN,
                score       DOUBLE PRECISION,
                created_at  TIMESTAMP,
                notes       TEXT
            )
        """)
        cur.execute("""
            INSERT INTO bench_data
            SELECT
                i,
                'user_' || i,
                'user_' || i || '@example.com',
                (ARRAY['Engineering','Marketing','Sales','HR','Finance'])[1 + (i %% 5)],
                30000 + (i %% 70000)::numeric + 0.50,
                (i %% 3 != 0),
                (i %% 1000)::double precision / 7.0,
                '2020-01-01'::timestamp + (i || ' seconds')::interval,
                'Notes for employee number ' || i
            FROM generate_series(1, %(n)s) AS s(i)
        """, {"n": row_count})
        cur.execute("ANALYZE bench_data")
    conn.commit()

    elapsed = time.perf_counter() - t0
    print(f"done in {elapsed:.1f}s")


# --- Benchmark runners ---

def bench_pigiaminja_copy(conn, row_count, warmup, iterations):
    """Benchmark A: COPY TO with pigiaminja jinja format."""
    copy_sql = (
        f"COPY (SELECT {COLUMNS} FROM bench_data) "
        f"TO STDOUT (FORMAT 'jinja', TEMPLATE '{JINJA_TEMPLATE}')"
    )
    times = []
    byte_count = 0
    line_count = 0

    for i in range(warmup + iterations):
        sink = ByteCounter()
        gc.disable()
        t0 = time.perf_counter()
        with conn.cursor().copy(copy_sql) as copy:
            for chunk in copy:
                sink.write(chunk)
        t1 = time.perf_counter()
        gc.enable()

        if i >= warmup:
            times.append(t1 - t0)
            byte_count = sink.byte_count
            line_count = sink.line_count

    return BenchResult("pigiaminja COPY jinja", row_count, times, byte_count, line_count)


def bench_native_copy_csv(conn, row_count, warmup, iterations):
    """Benchmark B: Native PostgreSQL COPY TO CSV (baseline)."""
    copy_sql = f"COPY (SELECT {COLUMNS} FROM bench_data) TO STDOUT WITH (FORMAT CSV)"
    times = []
    byte_count = 0
    line_count = 0

    for i in range(warmup + iterations):
        sink = ByteCounter()
        gc.disable()
        t0 = time.perf_counter()
        with conn.cursor().copy(copy_sql) as copy:
            for chunk in copy:
                sink.write(chunk)
        t1 = time.perf_counter()
        gc.enable()

        if i >= warmup:
            times.append(t1 - t0)
            byte_count = sink.byte_count
            line_count = sink.line_count

    return BenchResult("Native COPY CSV", row_count, times, byte_count, line_count)


def bench_psycopg_client(conn, row_count, warmup, iterations):
    """Benchmark C: SELECT + Python client-side CSV formatting."""
    select_sql = f"SELECT {COLUMNS} FROM bench_data"
    times = []
    byte_count = 0
    line_count = 0

    for i in range(warmup + iterations):
        sink = ByteCounter()
        gc.disable()
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            for row in cur.stream(select_sql):
                line = ",".join(str(v) for v in row) + "\n"
                sink.write(line)
        t1 = time.perf_counter()
        gc.enable()

        if i >= warmup:
            times.append(t1 - t0)
            byte_count = sink.byte_count
            line_count = sink.line_count

    return BenchResult("psycopg client-side CSV", row_count, times, byte_count, line_count)


# --- Output ---

def print_results(all_results):
    """Print a formatted results table."""
    print("\n" + "=" * 80)
    print("  pigiaminja Benchmark Results")
    print("=" * 80)

    for row_count, results in all_results.items():
        print(f"\n--- {row_count:,} rows ---\n")
        header = f"{'Approach':<28} {'Min(s)':>8} {'Max(s)':>8} {'Mean(s)':>8} {'Median(s)':>10} {'StdDev':>8} {'rows/s':>12}"
        print(header)
        print("-" * len(header))

        for r in results:
            print(
                f"{r.approach:<28} "
                f"{min(r.times):8.3f} "
                f"{max(r.times):8.3f} "
                f"{r.mean:8.3f} "
                f"{r.median:10.3f} "
                f"{r.stdev:8.4f} "
                f"{r.throughput:12,.0f}"
            )

        # Print ratios
        jinja_r = results[0]
        csv_r = results[1]
        client_r = results[2]

        print(f"\n  Ratios (higher = faster wins):")

        def ratio_str(a_name, a_mean, b_name, b_mean):
            if a_mean < b_mean:
                return f"{a_name} is {b_mean / a_mean:.2f}x faster than {b_name}"
            else:
                return f"{a_name} is {a_mean / b_mean:.2f}x slower than {b_name}"

        if csv_r.mean > 0:
            print(f"    {ratio_str('pigiaminja', jinja_r.mean, 'native CSV', csv_r.mean)}")
        if client_r.mean > 0:
            print(f"    {ratio_str('pigiaminja', jinja_r.mean, 'psycopg', client_r.mean)}")
        if client_r.mean > 0 and csv_r.mean > 0:
            print(f"    {ratio_str('native CSV', csv_r.mean, 'psycopg', client_r.mean)}")

        print(f"\n  Data sizes:")
        for r in results:
            print(f"    {r.approach:<28} {r.byte_count:>12,} bytes  ({r.line_count:>10,} lines)")


def emit_json(all_results):
    """Emit machine-readable JSON results."""
    output = {}
    for row_count, results in all_results.items():
        output[str(row_count)] = []
        for r in results:
            output[str(row_count)].append({
                "approach": r.approach,
                "row_count": r.row_count,
                "times": r.times,
                "mean": r.mean,
                "median": r.median,
                "stdev": r.stdev,
                "throughput": r.throughput,
                "byte_count": r.byte_count,
                "line_count": r.line_count,
            })
    print("\n--- JSON Results ---")
    print(json.dumps(output, indent=2))


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="pigiaminja benchmark")
    parser.add_argument(
        "--row-counts", nargs="+", type=int, default=ROW_COUNTS,
        help="Row counts to benchmark (default: 10000 100000 1000000)"
    )
    parser.add_argument("--iterations", type=int, default=BENCH_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=WARMUP_ITERATIONS)
    parser.add_argument("--json", action="store_true", help="Also emit JSON output")
    parser.add_argument("--dsn", default=DSN, help="PostgreSQL connection string")
    args = parser.parse_args()

    print("Connecting to PostgreSQL...")
    conn = psycopg.connect(args.dsn, autocommit=True)
    pg_version = conn.execute("SELECT version()").fetchone()[0]
    print(f"  {pg_version}")

    # Verify pigiaminja is loaded
    try:
        conn.execute("SHOW pigiaminja.enable_copy_hooks")
        print("  pigiaminja extension: loaded")
    except Exception as e:
        print(f"  ERROR: pigiaminja not loaded: {e}")
        sys.exit(1)

    print(f"\nBenchmark config: {args.warmup} warmup + {args.iterations} timed iterations")

    all_results = {}

    for row_count in args.row_counts:
        print(f"\n{'=' * 40}")
        print(f"Benchmarking {row_count:,} rows")
        print(f"{'=' * 40}")

        setup_schema(conn, row_count)

        print(f"  Running pigiaminja COPY jinja...", flush=True)
        r1 = bench_pigiaminja_copy(conn, row_count, args.warmup, args.iterations)
        print(f"    mean={r1.mean:.3f}s  ({r1.throughput:,.0f} rows/s)")

        print(f"  Running native COPY CSV...", flush=True)
        r2 = bench_native_copy_csv(conn, row_count, args.warmup, args.iterations)
        print(f"    mean={r2.mean:.3f}s  ({r2.throughput:,.0f} rows/s)")

        print(f"  Running psycopg client-side CSV...", flush=True)
        r3 = bench_psycopg_client(conn, row_count, args.warmup, args.iterations)
        print(f"    mean={r3.mean:.3f}s  ({r3.throughput:,.0f} rows/s)")

        # Validate: native CSV and psycopg should produce row_count lines.
        # pigiaminja COPY sends each row as a separate protocol message without
        # trailing newlines, so line_count will be 0 - that's expected.
        for r in [r2, r3]:
            if r.line_count != row_count:
                print(f"  WARNING: {r.approach} produced {r.line_count} lines, expected {row_count}")

        all_results[row_count] = [r1, r2, r3]

    print_results(all_results)

    if args.json:
        emit_json(all_results)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
