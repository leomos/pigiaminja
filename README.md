# pigiaminja - PostgreSQL Jinja Template Extension

A PostgreSQL extension that adds Jinja template format support to the `COPY TO` command.

```sql
pigiaminja=# COPY (
  SELECT * FROM (
    VALUES
      ('Alice', 'Engineering', 85000),
      ('Bob', 'Marketing', 62000),
      ('Carol', 'Sales', 71000)
  ) AS emp(name, department, salary)
) TO STDOUT (FORMAT 'jinja', TEMPLATE '
<tr>
    <td>{{ row.name }}</td>
    <td>{{ row.department }}</td>
    <td>${{ row.salary }}</td>
</tr>
');

<tr>
    <td>Alice</td>
    <td>Engineering</td>
    <td>$85000</td>
</tr>
<tr>
    <td>Bob</td>
    <td>Marketing</td>
    <td>$62000</td>
</tr>
<tr>
    <td>Carol</td>
    <td>Sales</td>
    <td>$71000</td>
</tr>pigiaminja=#
```

## Getting started

### Using Docker

The easiest way to try this is by using a pre-built docker image that contains the extension, `ghcr.io/leomos/pigiaminja`:

```
$ docker run --name some-postgres -e POSTGRES_PASSWORD=mysecretpassword -d ghcr.io/leomos/pigiaminja
$ docker exec -it some-postgres psql -U postgres
psql (17.5 (Debian 17.5-1.pgdg120+1))
Type "help" for help.

postgres=# CREATE EXTENSION pigiaminja;
CREATE EXTENSION
postgres=# COPY (
  SELECT * FROM (
    VALUES
      ('Alice', 'Engineering', 85000),
      ('Bob', 'Marketing', 62000),
      ('Carol', 'Sales', 71000)
  ) AS emp(name, department, salary)
) TO STDOUT (FORMAT 'jinja', TEMPLATE '
<tr>
    <td>{{ row.name }}</td>
    <td>{{ row.department }}</td>
    <td>${{ row.salary }}</td>
</tr>
');

<tr>
    <td>Alice</td>
    <td>Engineering</td>
    <td>$85000</td>
</tr>
<tr>
    <td>Bob</td>
    <td>Marketing</td>
    <td>$62000</td>
</tr>
<tr>
    <td>Carol</td>
    <td>Sales</td>
    <td>$71000</td>
</tr>postgres=#
```

The `pigiaminja` image is a debian PostgreSQL 17 image with the pre-compiled extension built into.

#### Building docker image

You can build the docker image with:

```
$ git clone https://github.com/leomos/pigiaminja
$ cd pigiaminja
$ docker build -t pigiaminja .
```

The `Dockerfile` accepts the `PG_MAJOR` argument to define the major version of the PostgreSQL image that pigiaminja will be built on.

The default PostgreSQL image version is `17` by it's been tested with `14`, `15` and `16`.

You can build for another version like this:

```
docker build --build-arg PG_MAJOR=14 -t pigiaminja:postgres_14 .
```

### Using source code

You can install the extension and start developing it by using `pgrx`.

Assuming you already have `cargo` installed:

```
$ git clone https://github.com/leomos/pigiaminja
$ cd pigiaminja
$ export CARGO_PGRX_VERSION=0.16.1
$ export PG_MAJOR=17
$ cargo install --force --locked cargo-pgrx@"${CARGO_PGRX_VERSION}"
$ cargo pgrx init 
# or if you have a locally installed postgres instance
$ cargo pgrx init --pg"${PG_MAJOR}" $(which pg_config)
$ echo "shared_preload_libraries = 'pigiaminja'" >> ~/.pgrx/data-"${PG_MAJOR}"/postgresql.con
$ cargo pgrx run --features pg"${PG_MAJOR}"
psql> "CREATE EXTENSION pigiaminja;"
```

## Benchmarks

The `benchmark/` directory contains a script that compares pigiaminja's `COPY TO (FORMAT 'jinja')` against two alternatives: native `COPY TO (FORMAT 'csv')` and a plain `SELECT` with the formatting done client-side in Python.

It needs a running PostgreSQL with pigiaminja loaded (the pgrx instance from the section above works fine, just check the DSN at the top of the script) and you can run it directly with [uv](https://docs.astral.sh/uv/), which takes care of the dependencies:

```
$ uv run benchmark/bench.py
```

For reference, on an M1 Max with PostgreSQL 18, exporting a million rows of a mixed-type table renders at ~435k rows/s: about 1.4x faster than formatting the same rows client-side with psycopg, and about 2.8x slower than native CSV, which is the price of rendering a template for every row.

There's also a profiler that attaches to the PostgreSQL backend while it runs a pigiaminja `COPY` and produces a flamegraph, in case you want to see where the time goes:

```
$ uv run benchmark/flamegraph.py
```

## Credits

Implementation of the extension internals is heavily inspired by [pg_parquet](https://github.com/CrunchyData/pg_parquet).
