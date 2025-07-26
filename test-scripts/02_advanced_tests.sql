-- Advanced tests using sample data from the table
\echo '=== Running advanced pigiaminja tests ==='

-- Test 1: Complex JSON with nested data
\echo 'Test 1: Complex JSON output'
COPY (SELECT name, age, salary, metadata FROM sample_data LIMIT 2) 
TO STDOUT WITH (FORMAT jinja, template '{
  "employee": {
    "name": "{{ name }}",
    "age": {{ age }},
    "salary": {{ salary }},
    "metadata": {{ metadata }}
  }
}');

-- Test 2: XML format
\echo 'Test 2: XML output'
COPY (SELECT name, email, age FROM sample_data LIMIT 3) 
TO STDOUT WITH (FORMAT jinja, template '<employee><name>{{ name }}</name><email>{{ email }}</email><age>{{ age }}</age></employee>');

-- Test 3: Custom report format
\echo 'Test 3: Custom report format'
COPY (SELECT name, salary, CASE WHEN salary > 70000 THEN 'Senior' ELSE 'Junior' END as level FROM sample_data) 
TO STDOUT WITH (FORMAT jinja, template 'Employee: {{ name }} | Salary: ${{ salary }} | Level: {{ level }}');

-- Test 4: TSV (Tab-separated values)
\echo 'Test 4: TSV format'
COPY (SELECT name, email, age FROM sample_data LIMIT 2) 
TO STDOUT WITH (FORMAT jinja, template '{{ name }}	{{ email }}	{{ age }}');

-- Test 5: YAML-like format
\echo 'Test 5: YAML-like format'
COPY (SELECT name, age, is_active FROM sample_data LIMIT 2) 
TO STDOUT WITH (FORMAT jinja, template '- name: {{ name }}
  age: {{ age }}
  active: {{ is_active }}');

\echo 'Advanced tests completed successfully!'