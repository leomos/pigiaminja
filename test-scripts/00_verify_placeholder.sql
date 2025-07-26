-- Test script to verify JINJA_EXTENTIONS_PLACEHOLDER output
-- This tests the basic functionality of the pigiaminja extension

-- Enable the jinja copy hook
SET pigiaminja.enable_jinja_copy_hook = true;

-- Create a simple test table
CREATE TABLE test_jinja_output (
    id INTEGER,
    name TEXT,
    value NUMERIC
);

-- Insert some test data
INSERT INTO test_jinja_output VALUES 
    (1, 'test1', 10.5),
    (2, 'test2', 20.0),
    (3, 'test3', 30.75);

-- Test 1: COPY TO STDOUT with FORMAT jinja
-- This should output exactly "JINJA_EXTENTIONS_PLACEHOLDER" to stdout
\echo '=== Test 1: COPY TO STDOUT with FORMAT jinja ==='
COPY (SELECT * FROM test_jinja_output) TO STDOUT WITH (FORMAT jinja);

-- Test 2: COPY table TO STDOUT with FORMAT jinja
-- This should also output exactly "JINJA_EXTENTIONS_PLACEHOLDER" to stdout
\echo '=== Test 2: COPY table TO STDOUT with FORMAT jinja ==='
COPY test_jinja_output TO STDOUT WITH (FORMAT jinja);

-- Test 3: COPY TO file with FORMAT jinja
-- This should write "JINJA_EXTENTIONS_PLACEHOLDER" to the file
\echo '=== Test 3: COPY TO file with FORMAT jinja ==='
COPY test_jinja_output TO '/tmp/jinja_test_output.txt' WITH (FORMAT jinja);

-- Verify the file contents
\echo '=== Verifying file contents ==='
\! cat /tmp/jinja_test_output.txt

-- Test 4: Regular COPY (should not be intercepted)
\echo '=== Test 4: Regular COPY TO STDOUT (control test) ==='
COPY test_jinja_output TO STDOUT WITH (FORMAT csv);

-- Test 5: COPY with jinja format disabled
\echo '=== Test 5: COPY with jinja hook disabled ==='
SET pigiaminja.enable_jinja_copy_hook = false;
COPY test_jinja_output TO STDOUT WITH (FORMAT jinja);

-- Clean up
DROP TABLE test_jinja_output;
\! rm -f /tmp/jinja_test_output.txt

\echo '=== Test completed ==='