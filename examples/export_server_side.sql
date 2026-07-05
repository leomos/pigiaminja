-- Server-side exports with pigiaminja: COPY straight to a file, or pipe
-- the rendered output through a program.
--
-- Both happen on the server, not on your machine: the file lands on the
-- server's filesystem and the program runs as the PostgreSQL server
-- process. Like regular COPY, that is why they require superuser or the
-- built-in pg_write_server_files / pg_execute_server_program roles. If
-- you want the file client-side, psql's \copy works for anyone by going
-- through STDOUT:
--
--   \copy (SELECT 'x' AS c) TO 'out.txt' (FORMAT 'jinja', TEMPLATE '{{ row.c }}')
--
-- Run this file with: psql -f examples/export_server_side.sql

-- Render each employee as an HTML table row into a server-side file.
COPY (
  SELECT * FROM (
    VALUES
      ('Alice', 'Engineering', 85000),
      ('Bob', 'Marketing', 62000),
      ('Carol', 'Sales', 71000)
  ) AS emp(name, department, salary)
) TO '/tmp/employees.html' (FORMAT 'jinja', TEMPLATE '
<tr>
    <td>{{ row.name }}</td>
    <td>{{ row.department }}</td>
    <td>${{ row.salary }}</td>
</tr>
');

-- Same rows, but piped through gzip: the program receives the rendered
-- output on stdin, so the compressed file is written as the rows render.
COPY (
  SELECT * FROM (
    VALUES
      ('Alice', 'Engineering', 85000),
      ('Bob', 'Marketing', 62000),
      ('Carol', 'Sales', 71000)
  ) AS emp(name, department, salary)
) TO PROGRAM 'gzip > /tmp/employees.html.gz' (FORMAT 'jinja', TEMPLATE '
<tr>
    <td>{{ row.name }}</td>
    <td>{{ row.department }}</td>
    <td>${{ row.salary }}</td>
</tr>
');

-- See what landed:
--   cat /tmp/employees.html
--   gunzip -c /tmp/employees.html.gz
