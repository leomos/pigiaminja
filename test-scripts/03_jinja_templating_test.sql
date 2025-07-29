-- Test script for Jinja templating functionality

-- Enable the Jinja copy hook
SET pigiaminja.enable_jinja_copy_hook = true;

-- Create a test table
DROP TABLE IF EXISTS test_users;
CREATE TABLE test_users (
    id INTEGER,
    name TEXT,
    email TEXT,
    age INTEGER,
    active BOOLEAN
);

-- Insert test data
INSERT INTO test_users VALUES 
    (1, 'Alice', 'alice@example.com', 30, true),
    (2, 'Bob', 'bob@example.com', 25, false),
    (3, 'Charlie', 'charlie@example.com', 35, true);

-- Create a simple Jinja template file
\! echo 'User: {{ row.name }} ({{ row.email }}) - Age: {{ row.age }}, Status: {% if row.active %}Active{% else %}Inactive{% endif %}' > /tmp/user_template.jinja

-- Test COPY with Jinja format and template
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_template.jinja');

-- Test with a SELECT query
COPY (SELECT * FROM test_users WHERE active = true) TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_template.jinja');

-- Create a more complex template
\! cat > /tmp/user_card.jinja <<EOF
================================
User Profile #{{ row.id }}
================================
Name: {{ row.name | upper }}
Email: {{ row.email }}
Age: {{ row.age }} years old
Account Status: {% if row.active %}✓ Active{% else %}✗ Inactive{% endif %}

EOF

-- Test with the complex template
COPY test_users TO STDOUT (FORMAT 'jinja', TEMPLATE '/tmp/user_card.jinja');

-- Clean up
DROP TABLE test_users;
\! rm -f /tmp/user_template.jinja /tmp/user_card.jinja