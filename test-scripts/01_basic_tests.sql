-- Basic test for pigiaminja extension functionality
\echo '=== Running basic pigiaminja tests ==='

-- Test 1: Simple CSV format with jinja template
\echo 'Test 1: Basic CSV output'
COPY (SELECT 'John' as name, 25 as age, 'john@example.com' as email) 
TO STDOUT WITH (FORMAT jinja, template '{{ name }},{{ age }},{{ email }}');

-- Test 2: JSON format
\echo 'Test 2: JSON output'
COPY (SELECT 'Jane' as name, 30 as age, 75000.50 as salary) 
TO STDOUT WITH (FORMAT jinja, template '{"name": "{{ name }}", "age": {{ age }}, "salary": {{ salary }}}');

-- Test 3: Custom delimiter
\echo 'Test 3: Custom delimiter'
COPY (SELECT 'Bob' as name, 'Engineer' as role, 'Active' as status) 
TO STDOUT WITH (FORMAT jinja, template '{{ name }}|{{ role }}|{{ status }}');

-- Test 4: HTML format
\echo 'Test 4: HTML table row'
COPY (SELECT 'Alice' as name, 'Manager' as role) 
TO STDOUT WITH (FORMAT jinja, template '<tr><td>{{ name }}</td><td>{{ role }}</td></tr>');

\echo 'Basic tests completed successfully!'