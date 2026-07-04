#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "psycopg[binary]>=3.1",
# ]
# ///
"""
Example: run a pigiaminja COPY from Python with the template read from a .jinja file.

The TEMPLATE option of COPY ... (FORMAT 'jinja') is a plain string, and COPY
options cannot be passed as query parameters, so the template file is read
client-side and inlined into the statement. psycopg's sql.Literal takes care
of quoting it safely, whatever quotes or backslashes the template contains.

The file content is the template, rendered once per row. One gotcha worth
knowing: like Jinja, minijinja strips a single trailing newline from the
template, which is why row.html.jinja ends with a blank line — one newline
gets eaten, the other keeps each row's output on its own line.

Usage:
  uv run examples/export.py
  uv run examples/export.py --template my.jinja --query "SELECT * FROM users"
  uv run examples/export.py --output employees.html
  uv run examples/export.py --dsn "host=localhost dbname=postgres user=postgres password=secret"
"""

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg import sql


if sys.platform == "darwin":
    DSN = "host=localhost port=28818 dbname=postgres"
else:
    DSN = "host=/var/run/postgresql dbname=postgres user=postgres password=benchpass"

DEFAULT_TEMPLATE = Path(__file__).parent / "row.html.jinja"

DEFAULT_QUERY = """\
SELECT * FROM (
  VALUES
    ('Alice', 'Engineering', 85000),
    ('Bob', 'Marketing', 62000),
    ('Carol', 'Sales', 71000)
) AS emp(name, department, salary)"""


def main():
    parser = argparse.ArgumentParser(
        description="Export query results through a Jinja template file via pigiaminja"
    )
    parser.add_argument(
        "--template", type=Path, default=DEFAULT_TEMPLATE,
        help="Path to the .jinja template, rendered once per row (default: row.html.jinja)",
    )
    parser.add_argument(
        "--query", default=DEFAULT_QUERY,
        help="Query whose rows get rendered (default: a small VALUES demo)",
    )
    parser.add_argument("--dsn", default=DSN, help="PostgreSQL connection string")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the rendered output here instead of stdout",
    )
    args = parser.parse_args()

    try:
        template = args.template.read_text()
    except OSError as e:
        sys.exit(f"cannot read template: {e}")

    copy_stmt = sql.SQL(
        "COPY ({query}) TO STDOUT (FORMAT 'jinja', TEMPLATE {template})"
    ).format(
        query=sql.SQL(args.query),
        template=sql.Literal(template),
    )

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        try:
            conn.execute("SHOW pigiaminja.enable_copy_hooks")
        except psycopg.Error:
            sys.exit(
                "pigiaminja is not loaded on this server "
                "(is it in shared_preload_libraries?)"
            )

        out = open(args.output, "wb") if args.output else sys.stdout.buffer
        try:
            with conn.cursor().copy(copy_stmt) as copy:
                for chunk in copy:
                    out.write(chunk)
        finally:
            if args.output:
                out.close()
                print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
