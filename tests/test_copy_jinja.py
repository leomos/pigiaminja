#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "psycopg[binary]>=3.1",
# ]
# ///
"""
Correctness tests for pigiaminja: COPY ... TO STDOUT (FORMAT 'jinja').

These run end-to-end against a live PostgreSQL with the extension preloaded
(same instance the benchmark uses). The Jinja DestReceiver only ever emits
rendered rows over the libpq COPY protocol, so the rendered bytes can only be
observed from a real client -- that is what this harness does.

Two kinds of assertions are used:

  * Golden output -- the exact rendered string. Used for type formatting whose
    representation is fixed by minijinja/PostgreSQL (floats, bools, json, the
    none/undefined distinction, ...). These double as a behavioural spec.

  * Differential oracle -- for "fairly complicated" queries (joins, CTEs,
    window functions, aggregates, subqueries) we run the SAME query as a plain
    SELECT and rebuild the expected output in Python with jinja_str(), which
    mirrors the extension's rendering rules. Non-trivial column types are cast
    to ::text inside the query so the oracle is exact. This validates values,
    column mapping, row ordering, row count and NULL handling without having to
    hand-compute every byte.

Usage:
  uv run tests/test_copy_jinja.py
  uv run tests/test_copy_jinja.py --dsn "host=localhost port=28818 dbname=postgres"
"""

import argparse
import sys

import psycopg

if sys.platform == "darwin":
    DSN = "host=localhost port=28818 dbname=postgres"
else:
    DSN = "host=/var/run/postgresql dbname=postgres user=postgres password=benchpass"

# Dollar-quote tag used to embed the Jinja template literally in the COPY SQL.
# No template or query below contains this token.
TAG = "pigi"


# --- Harness -----------------------------------------------------------------

class Harness:
    def __init__(self, dsn):
        self.dsn = dsn
        self.passed = 0
        self.failed = 0
        self.connect()

    def connect(self):
        self.conn = psycopg.connect(self.dsn, autocommit=True)

    def reconnect(self):
        try:
            self.conn.close()
        except Exception:
            pass
        # The server may still be in crash recovery; retry briefly.
        import time
        for _ in range(50):
            try:
                self.connect()
                return
            except Exception:
                time.sleep(0.2)
        self.connect()

    def render(self, query, template):
        """Run a jinja COPY and return the full rendered output as str."""
        sql = (
            f"COPY ({query}) TO STDOUT "
            f"(FORMAT 'jinja', TEMPLATE ${TAG}${template}${TAG}$)"
        )
        data = bytearray()
        with self.conn.cursor().copy(sql) as copy:
            for chunk in copy:
                data += chunk
        return bytes(data).decode("utf-8")

    def fetch(self, query):
        with self.conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()

    def check(self, name, got, expected):
        if got == expected:
            self.passed += 1
            print(f"  \033[32m✓\033[0m {name}")
        else:
            self.failed += 1
            print(f"  \033[31m✗ {name}\033[0m")
            print(f"      expected: {_clip(expected)}")
            print(f"      got:      {_clip(got)}")

    def golden(self, name, query, template, expected):
        """Assert the rendered output exactly equals `expected`."""
        try:
            got = self.render(query, template)
        except Exception as e:
            self.failed += 1
            print(f"  \033[31m✗ {name}  (raised: {e})\033[0m")
            self.reconnect()
            return
        self.check(name, got, expected)

    def differential(self, name, query, columns, fsep=",", rowpfx="\n"):
        """
        Build a row template that PREFIXES each row with `rowpfx` (a newline by
        default) and joins `columns` with `fsep`, render `query` through it, and
        compare against an oracle that runs the same query as a plain SELECT and
        rebuilds the output positionally with jinja_str().

        The newline goes at the *start* of each row, not the end: minijinja
        (Jinja2-compatible) strips a single trailing newline from a template, so
        a trailing row terminator would vanish and the rows would run together
        (see test_trailing_newline_behavior). A leading newline is never the
        trailing character, so it survives.

        Generating the template here from the same column list the oracle
        iterates guarantees the two agree by construction, so a mismatch means
        the extension rendered a value/order/count wrong.
        """
        template = rowpfx + fsep.join("{{row.%s}}" % c for c in columns)
        try:
            got = self.render(query, template)
        except Exception as e:
            self.failed += 1
            print(f"  \033[31m✗ {name}  (raised: {e})\033[0m")
            self.reconnect()
            return
        rows = self.fetch(query)
        expected = "".join(rowpfx + fsep.join(jinja_str(c) for c in row) for row in rows)
        self.check(name, got, expected)


def _clip(s, limit=180):
    r = repr(s)
    if len(r) > limit:
        return r[:limit] + f"... (+{len(r) - limit} chars)"
    return r


def jinja_str(v):
    """Mirror the extension's rendering for the column types used by the
    differential queries (int / text / bool / NULL; everything else is cast to
    ::text in the query so it arrives here as a plain string)."""
    if v is None:
        return "none"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


# --- Schema ------------------------------------------------------------------

def setup(h):
    cur = h.conn.cursor()
    cur.execute("DROP TABLE IF EXISTS employees, departments, docs")
    cur.execute("""
        CREATE TABLE departments (
            id    int PRIMARY KEY,
            dname text
        )
    """)
    cur.execute("""
        CREATE TABLE employees (
            id       int PRIMARY KEY,
            name     text,
            dept_id  int,
            salary   numeric(10,2),
            manager  text
        )
    """)
    cur.execute("""
        CREATE TABLE docs (
            id   int PRIMARY KEY,
            data jsonb,
            raw  json
        )
    """)
    cur.execute("""
        INSERT INTO departments (id, dname) VALUES
            (1, 'Engineering'), (2, 'Sales'), (3, 'Finance')
    """)
    cur.execute("""
        INSERT INTO employees (id, name, dept_id, salary, manager) VALUES
            (1, 'Alice',   1, 9000.00, NULL),
            (2, 'Bob',     1, 6000.00, 'Alice'),
            (3, 'Carol',   1, 4500.00, 'Alice'),
            (4, 'Dave',    2, 7000.00, NULL),
            (5, 'Eve',     2, 5500.00, 'Dave'),
            (6, 'Frank',   3, 8000.00, NULL),
            (7, 'Grace',   3, 3000.00, 'Frank')
    """)
    cur.execute("""
        INSERT INTO docs (id, data, raw) VALUES
            (1,
             '{"a": 1, "b": {"c": "deep"}, "tags": ["x", "y"]}'::jsonb,
             '{"a": 1, "b": {"c": "deep"}, "tags": ["x", "y"]}'::json),
            (2,
             '{"a": 2, "b": {"c": "shallow"}, "tags": []}'::jsonb,
             '{"a": 2, "b": {"c": "shallow"}, "tags": []}'::json)
    """)


# --- Tests -------------------------------------------------------------------

def test_type_rendering(h):
    print("\nType rendering (golden):")
    query = (
        "SELECT 1::int2 AS i2, 2::int4 AS i4, 3::int8 AS i8, "
        "1.5::float4 AS f4, 3.0::float8 AS f8, (1.0/3.0)::float8 AS third, "
        "'nan'::float8 AS nanv, 'inf'::float8 AS infv, "
        "30000.50::numeric(10,2) AS num, true AS bt, false AS bf, "
        "'plain text'::text AS txt, 'x'::char(3) AS ch, "
        "'2020-01-01 12:34:56'::timestamp AS ts, '2020-03-15'::date AS d, "
        "'11111111-2222-3333-4444-555555555555'::uuid AS u, "
        "ARRAY[1,2,3]::int[] AS arr, NULL::text AS nul"
    )
    tmpl = (
        "i2={{row.i2}};i4={{row.i4}};i8={{row.i8}};"
        "f4={{row.f4}};f8={{row.f8}};third={{row.third}};"
        "nan={{row.nanv}};inf={{row.infv}};num={{row.num}};"
        "bt={{row.bt}};bf={{row.bf}};txt={{row.txt}};ch=[{{row.ch}}];"
        "ts={{row.ts}};d={{row.d}};u={{row.u}};arr={{row.arr}};nul={{row.nul}}"
    )
    expected = (
        "i2=1;i4=2;i8=3;"
        "f4=1.5;f8=3.0;third=0.3333333333333333;"
        "nan=none;inf=none;num=30000.50;"
        "bt=true;bf=false;txt=plain text;ch=[x  ];"
        "ts=2020-01-01 12:34:56;d=2020-03-15;"
        "u=11111111-2222-3333-4444-555555555555;arr={1,2,3};nul=none"
    )
    h.golden("scalar types render as expected", query, tmpl, expected)


def test_null_and_undefined(h):
    print("\nNULL vs undefined (golden):")
    # An explicit SQL NULL becomes minijinja `none` (renders as "none").
    h.golden("null int -> none", "SELECT NULL::int AS x", "[{{row.x}}]", "[none]")
    h.golden("null text -> none", "SELECT NULL::text AS x", "[{{row.x}}]", "[none]")
    # A column the template references but the row does not have is *undefined*
    # (renders empty) -- distinct from none.
    h.golden("missing column -> empty", "SELECT 1 AS x", "[{{row.nope}}]", "[]")
    # default() replaces undefined but NOT none ...
    h.golden("default on missing -> NA", "SELECT 1 AS x",
             "{{ row.nope | default('NA') }}", "NA")
    h.golden("default on null -> none", "SELECT NULL::int AS x",
             "{{ row.x | default('NA') }}", "none")
    # ... unless the truthy form of default() is used.
    h.golden("default(truthy) on null -> NA", "SELECT NULL::int AS x",
             "{{ row.x | default('NA', true) }}", "NA")


def test_text_safety(h):
    print("\nText passthrough / injection safety (golden):")
    # autoescape is off: HTML metacharacters pass through verbatim.
    h.golden("no html escaping", "SELECT '<a> & \"b\"'::text AS x",
             "[{{row.x}}]", '[<a> & "b"]')
    # Data that *looks* like a Jinja expression must NOT be evaluated.
    h.golden("no template injection from data",
             "SELECT '{{ 7 * 7 }}'::text AS x", "[{{row.x}}]", "[{{ 7 * 7 }}]")
    h.golden("no statement injection from data",
             "SELECT '{% raw %}danger{% endraw %}'::text AS x",
             "[{{row.x}}]", "[{% raw %}danger{% endraw %}]")
    # Unicode and embedded delimiters survive.
    h.golden("unicode + delimiters preserved",
             "SELECT 'café ☕, x\ty'::text AS x", "[{{row.x}}]", "[café ☕, x\ty]")


def test_jsonb(h):
    print("\nJSONB access (golden):")
    q = "SELECT data AS j FROM docs WHERE id = 1"
    h.golden("jsonb whole object", q, "{{row.j}}",
             '{"a": 1, "b": {"c": "deep"}, "tags": ["x", "y"]}')
    h.golden("jsonb nested subfield", q, "{{row.j.b.c}}", "deep")
    h.golden("jsonb scalar field", q, "{{row.j.a}}", "1")
    h.golden("jsonb array index", q, "{{row.j.tags[0]}}-{{row.j.tags[1]}}", "x-y")
    h.golden("jsonb array loop", q,
             "{% for t in row.j.tags %}[{{t}}]{% endfor %}", "[x][y]")


def test_json_text(h):
    # Regression: a `json` (text) column was previously decoded with the binary
    # jsonb decoder, which read the wrong varlena layout and crashed the
    # backend. It must behave like jsonb now.
    print("\nJSON (text type) access -- crash regression (golden):")
    q = "SELECT raw AS j FROM docs WHERE id = 1"
    h.golden("json whole object", q, "{{row.j}}",
             '{"a": 1, "b": {"c": "deep"}, "tags": ["x", "y"]}')
    h.golden("json nested subfield", q, "{{row.j.b.c}}", "deep")
    h.golden("json array loop", q,
             "{% for t in row.j.tags %}[{{t}}]{% endfor %}", "[x][y]")


def test_template_logic(h):
    print("\nTemplate control flow & filters (golden):")
    q = ("SELECT 'alice'::text AS name, 9000::int AS salary, "
         "NULL::text AS manager")
    h.golden("if/else on numeric", q,
             "{% if row.salary >= 5000 %}senior{% else %}junior{% endif %}",
             "senior")
    h.golden("upper filter", q, "{{ row.name | upper }}", "ALICE")
    h.golden("arithmetic in template", q, "{{ row.salary * 12 }}", "108000")
    h.golden("string concat", q, "{{ row.name ~ '@corp' }}", "alice@corp")
    h.golden("default for null manager", q,
             "{{ row.manager | default('(none)', true) }}", "(none)")


def test_empty_resultset(h):
    print("\nEdge cases:")
    h.golden("empty result set -> empty output",
             "SELECT 1 AS x WHERE false", "{{row.x}}\n", "")
    h.golden("single column, single row",
             "SELECT 42 AS x", "{{row.x}}", "42")


def test_trailing_newline_behavior(h):
    # pigiaminja renders each row through the template independently, and
    # minijinja keeps Jinja2's keep_trailing_newline=false default: a single
    # trailing newline in the template source is stripped. These tests pin that
    # behavior down so it can't change silently, and show the workaround.
    print("\nTrailing-newline handling (Jinja2 default -- golden):")
    q3 = "SELECT i AS x FROM generate_series(1, 3) AS s(i) ORDER BY i"
    h.golden("trailing newline stripped -> rows run together",
             q3, "{{row.x}}\n", "123")
    # Workaround: terminate rows with a *leading* newline instead.
    h.golden("leading newline separates rows",
             q3, "\n{{row.x}}", "\n1\n2\n3")
    # Only the trailing newline is special; interior newlines survive.
    h.golden("interior newline preserved",
             q3, "{{row.x}}\nEND", "1\nEND2\nEND3\nEND")


def test_complex_join(h):
    print("\nComplex query: JOIN + CASE + computed (differential):")
    query = (
        "SELECT e.id, e.name, d.dname, "
        "(e.salary * 12)::text AS annual, "
        "CASE WHEN e.salary >= 6000 THEN 'high' ELSE 'low' END AS band, "
        "(e.salary >= 6000) AS is_high "
        "FROM employees e JOIN departments d ON e.dept_id = d.id "
        "ORDER BY e.id"
    )
    cols = ["id", "name", "dname", "annual", "band", "is_high"]
    h.differential("join with case/computed/bool", query, cols, fsep=",")


def test_complex_cte_aggregate(h):
    print("\nComplex query: CTE + GROUP BY aggregate (differential):")
    query = (
        "WITH stats AS ("
        "  SELECT dept_id, count(*) AS n, sum(salary)::text AS total, "
        "         round(avg(salary), 2)::text AS avg_sal, "
        "         max(salary)::text AS top "
        "  FROM employees GROUP BY dept_id"
        ") SELECT dept_id, n, total, avg_sal, top FROM stats ORDER BY dept_id"
    )
    cols = ["dept_id", "n", "total", "avg_sal", "top"]
    h.differential("cte + group by", query, cols, fsep="|")


def test_complex_window(h):
    print("\nComplex query: window functions (differential):")
    query = (
        "SELECT id, name, dept_id, "
        "row_number() OVER (PARTITION BY dept_id ORDER BY salary DESC, id) AS rnk, "
        "(sum(salary) OVER (PARTITION BY dept_id ORDER BY id))::text AS running "
        "FROM employees ORDER BY dept_id, id"
    )
    cols = ["id", "name", "dept_id", "rnk", "running"]
    h.differential("window row_number + running sum", query, cols, fsep="|")


def test_complex_subquery(h):
    print("\nComplex query: subquery + filter + LIMIT (differential):")
    query = (
        "SELECT id, name, salary::text AS salary "
        "FROM employees "
        "WHERE salary > (SELECT avg(salary) FROM employees) "
        "ORDER BY salary DESC, id LIMIT 3"
    )
    cols = ["id", "name", "salary"]
    h.differential("subquery filter + order + limit", query, cols, fsep=";")


def test_scale_ordering(h):
    print("\nScale & ordering integrity (differential):")
    query = (
        "SELECT i AS id, ('row_' || i) AS label, mod(i, 7) AS m "
        "FROM generate_series(1, 1000) AS s(i) ORDER BY i"
    )
    cols = ["id", "label", "m"]
    h.differential("1000 rows, order preserved", query, cols, fsep=":")


def test_jsonb_query_driven(h):
    print("\nComplex query: per-row JSON logic (golden):")
    # Iterate docs, branch on tag presence, access nested fields. Rows are
    # terminated with a leading newline (a trailing one would be stripped).
    query = ("SELECT id, data AS j FROM docs ORDER BY id")
    tmpl = (
        "\nid={{row.id}} c={{row.j.b.c}} "
        "tags={% if row.j.tags %}{% for t in row.j.tags %}{{t}};{% endfor %}"
        "{% else %}(empty){% endif %}"
    )
    expected = (
        "\nid=1 c=deep tags=x;y;"
        "\nid=2 c=shallow tags=(empty)"
    )
    h.golden("per-row json branching", query, tmpl, expected)


# --- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="pigiaminja correctness tests")
    parser.add_argument("--dsn", default=DSN, help="PostgreSQL connection string")
    args = parser.parse_args()

    print("Connecting to PostgreSQL...")
    h = Harness(args.dsn)
    pg_version = h.conn.execute("SELECT version()").fetchone()[0]
    print(f"  {pg_version.split(',')[0]}")
    try:
        h.conn.execute("SHOW pigiaminja.enable_copy_hooks")
        print("  pigiaminja extension: loaded")
    except Exception as e:
        print(f"  ERROR: pigiaminja not loaded: {e}")
        sys.exit(1)

    setup(h)

    test_type_rendering(h)
    test_null_and_undefined(h)
    test_text_safety(h)
    test_jsonb(h)
    test_json_text(h)
    test_template_logic(h)
    test_empty_resultset(h)
    test_trailing_newline_behavior(h)
    test_complex_join(h)
    test_complex_cte_aggregate(h)
    test_complex_window(h)
    test_complex_subquery(h)
    test_scale_ordering(h)
    test_jsonb_query_driven(h)

    print("\n" + "=" * 60)
    total = h.passed + h.failed
    if h.failed == 0:
        print(f"  \033[32mAll {total} checks passed.\033[0m")
    else:
        print(f"  \033[31m{h.failed} of {total} checks FAILED.\033[0m")
    print("=" * 60)

    h.conn.close()
    sys.exit(1 if h.failed else 0)


if __name__ == "__main__":
    main()
