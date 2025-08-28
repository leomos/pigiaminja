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

The easiest way to try this is by building a docker image that contains the extension:

```
$ git clone https://github.com/leomos/pigiaminja
$ cd pigiaminja
$ docker build -t pigiaminja .
```

You can then use it as normal PostgreSQL docker image:

```
$ docker run --name some-postgres -e POSTGRES_PASSWORD=mysecretpassword -d pigiaminja
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
$ export CARGO_PGRX_VERSION=0.15.0
$ export PG_MAJOR=17
$ cargo install --force --locked cargo-pgrx@"${CARGO_PGRX_VERSION}"
$ cargo pgrx init 
# or if you have a locally installed postgres instance
$ cargo pgrx init --pg"${PG_MAJOR}" $(which pg_config)
$ echo "shared_preload_libraries = 'pigiaminja'" >> ~/.pgrx/data-"${PG_MAJOR}"/postgresql.conf

$ cargo pgrx run --features pg"${PG_MAJOR}"
psql> "CREATE EXTENSION pigiaminja;"
```
