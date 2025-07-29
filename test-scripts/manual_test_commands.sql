-- Manual test commands for Jinja templating functionality
-- Run these commands in the PostgreSQL prompt after cargo pgrx run completes

-- Step 1: Create the extension
CREATE EXTENSION pigiaminja;

-- Step 2: Enable the Jinja copy hook
SET pigiaminja.enable_jinja_copy_hook = true;

-- Step 3: Create test table and insert data
DROP TABLE IF EXISTS test_users;
CREATE TABLE test_users (
    id INTEGER,
    name TEXT,
    email TEXT,
    age INTEGER,
    active BOOLEAN
);

INSERT INTO test_users VALUES 
    (1, 'Alice', 'alice@example.com', 30, true),
    (2, 'Bob', 'bob@example.com', 25, false),
    (3, 'Charlie', 'charlie@example.com', 35, true),
    (4, 'Diana', 'diana@example.com', 28, true),
    (5, 'Eve', 'eve@example.com', 32, false);

-- Step 4: Create a simple template file (run in shell before SQL)
-- Run this in a separate terminal:
-- echo 'User: {{ row.name }} ({{ row.email }}) - Age: {{ row.age }}, Status: {% if row.active %}Active{% else %}Inactive{% endif %}' > /tmp/user_template.jinja

-- Step 5: Test basic COPY with Jinja format
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_template.jinja');

-- Step 6: Test COPY with SELECT query
COPY (SELECT * FROM test_users WHERE active = true) TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_template.jinja');

-- Step 7: Create a more complex template (run in shell)
-- cat > /tmp/user_json.jinja << 'EOF'
-- {
--   "id": {{ row.id }},
--   "name": "{{ row.name }}",
--   "email": "{{ row.email }}",
--   "age": {{ row.age }},
--   "active": {{ row.active | lower }}
-- }{% if not loop.last %},{% endif %}
-- EOF

-- Step 8: Test with JSON-like template
-- First line: [
-- Then run: COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_json.jinja');
-- Last line: ]

-- Step 9: Create a CSV-like template (run in shell)
-- echo '{{ row.id }},{{ row.name }},{{ row.email }},{{ row.age }},{{ row.active }}' > /tmp/user_csv.jinja

-- Step 10: Test CSV-like output
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_csv.jinja');

-- Step 11: Create an HTML template (run in shell)
-- cat > /tmp/user_html.jinja << 'EOF'
-- <tr>
--   <td>{{ row.id }}</td>
--   <td>{{ row.name | upper }}</td>
--   <td><a href="mailto:{{ row.email }}">{{ row.email }}</a></td>
--   <td>{{ row.age }}</td>
--   <td>{% if row.active %}<span style="color: green;">✓</span>{% else %}<span style="color: red;">✗</span>{% endif %}</td>
-- </tr>
-- EOF

-- Step 12: Test HTML output
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_html.jinja');

-- Step 13: Test error handling - missing template
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/nonexistent.jinja');

-- Step 14: Test error handling - missing template option
COPY test_users TO STDOUT (FORMAT 'jinja');

-- Step 15: Clean up
DROP TABLE test_users;