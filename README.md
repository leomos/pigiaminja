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
