# pigiaminja - PostgreSQL Jinja Template Extension

A PostgreSQL extension that adds Jinja template format support to the `COPY TO` command. This is a prototype implementation that demonstrates how to extend PostgreSQL's COPY command with custom format handlers.

## 🚀 Features

- ✅ Adds `FORMAT jinja` support to PostgreSQL's `COPY TO` command
- ✅ Returns placeholder string "JINJA_EXTENTIONS_PLACEHOLDER" for prototype
- ✅ Configurable via GUC setting `pigiaminja.enable_copy_hooks`
- ✅ Compatible with PostgreSQL 14, 15, 16, and 17
- ✅ Built using pgrx framework for robust PostgreSQL integration
- ✅ Comprehensive test coverage with pgrx test framework
- ✅ Docker setup for multi-version testing

## 📋 Requirements

- Rust toolchain (stable)
- pgrx (`cargo install cargo-pgrx`)
- pgrx initialized (`cargo pgrx init`)
- PostgreSQL 14+ (tested on 14, 15, 16, 17)

## 🔧 Installation

### Method 1: Direct Installation

```bash
# Build and install the extension
cargo pgrx install

# Configure PostgreSQL to load the extension
# Add to postgresql.conf:
shared_preload_libraries = 'pigiaminja'

# Restart PostgreSQL server
sudo systemctl restart postgresql  # or your method

# Connect to your database and create the extension
psql -d your_database
CREATE EXTENSION pigiaminja;
```

### Method 2: Development Installation

```bash
# Clone the repository
git clone <repository_url>
cd pigiaminja

# Install for specific PostgreSQL version
cargo pgrx install --release pg15

# Or install for all supported versions
cargo pgrx install --release
```

## 📖 Usage

### Basic Usage

```sql
-- Verify extension is loaded
SELECT * FROM pg_extension WHERE extname = 'pigiaminja';

-- Check configuration
SHOW pigiaminja.enable_copy_hooks;

-- Use FORMAT jinja in COPY TO commands
COPY (SELECT 1, 'hello', 3.14) TO STDOUT WITH (FORMAT jinja);
-- Output: JINJA_EXTENTIONS_PLACEHOLDER
```

### Configuration

```sql
-- Enable/disable functionality
SET pigiaminja.enable_copy_hooks = true;   -- Enable (default)
SET pigiaminja.enable_copy_hooks = false;  -- Disable

-- Permanent configuration in postgresql.conf
-- pigiaminja.enable_copy_hooks = true
```

### Examples

```sql
-- Example 1: Simple query output
COPY (SELECT generate_series(1,3)) TO STDOUT WITH (FORMAT jinja);

-- Example 2: Complex query with joins
COPY (
  SELECT u.name, u.email, p.title 
  FROM users u 
  JOIN posts p ON u.id = p.user_id 
  LIMIT 10
) TO STDOUT WITH (FORMAT jinja);

-- All return: JINJA_EXTENTIONS_PLACEHOLDER
```

## 🧪 Development & Testing

### Running Tests

```bash
# Test specific PostgreSQL version
cargo pgrx test pg15

# Test all supported versions
for version in pg14 pg15 pg16 pg17; do
    echo "Testing $version..."
    cargo pgrx test $version
done
```

### Test Coverage

The extension includes comprehensive tests:
- ✅ Extension loading and configuration
- ✅ GUC parameter functionality
- ✅ Hook enable/disable behavior
- ✅ COPY command interception
- ✅ Multi-version compatibility

### Docker Testing

```bash
# Build and test all PostgreSQL versions
docker-compose up --build

# Test specific version
docker-compose up postgres15

# Run test scripts
docker exec -i pigiaminja_postgres15_1 psql -U postgres -d pigiaminja < test-scripts/01_basic_tests.sql
```

## 🔧 Configuration Reference

### GUC Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pigiaminja.enable_copy_hooks` | boolean | `true` | Enable/disable jinja copy hook functionality |

### Required PostgreSQL Configuration

**Critical**: The extension MUST be loaded via `shared_preload_libraries`:

```ini
# postgresql.conf
shared_preload_libraries = 'pigiaminja'  # Required for ProcessUtility hooks
```

Without this configuration, PostgreSQL will reject the "jinja" format before the extension can process it.

## 🏗️ Architecture

### ProcessUtility Hook Implementation

The extension uses PostgreSQL's ProcessUtility hook mechanism:

1. **Hook Registration**: `_PG_init()` registers the ProcessUtility hook
2. **Command Interception**: `jinja_copy_hook()` intercepts all utility commands
3. **COPY Detection**: `is_copy_to_jinja_stmt()` identifies COPY TO with FORMAT jinja
4. **Output Generation**: `execute_copy_to_jinja()` outputs placeholder using PostgreSQL COPY protocol

### File Structure

```
pigiaminja/
├── src/
│   ├── lib.rs                    # Extension entry point
│   ├── copy_hook/
│   │   ├── mod.rs               # Module exports
│   │   ├── hook.rs              # ProcessUtility hook implementation
│   │   └── copy_to.rs           # COPY TO jinja execution logic
│   └── pgrx_tests/
│       └── mod.rs               # Comprehensive test suite
├── pigiaminja.control           # Extension control file
├── Cargo.toml                   # Rust project configuration
├── docker-compose.yml          # Multi-version testing setup
└── test-scripts/               # SQL test scripts
```

## 🚧 Current Limitations

This is a **prototype implementation**. Current limitations:

- ❌ No actual Jinja2 template processing (returns placeholder)
- ❌ No template parameter support
- ❌ No COPY FROM jinja support
- ❌ COPY TO file not fully implemented (uses notice output)

## 🔮 Future Enhancements

- [ ] Full Jinja2 template engine integration (using [Tera](https://crates.io/crates/tera))
- [ ] Template parameter passing via COPY options
- [ ] COPY FROM jinja support for template-based data import
- [ ] File output support for COPY TO with jinja format
- [ ] Advanced template features (loops, conditionals, filters)
- [ ] Template caching and optimization
- [ ] Error handling and debugging features

## 🐛 Troubleshooting

### Extension Not Loading

```sql
-- Check if extension is properly loaded
SELECT * FROM pg_extension WHERE extname = 'pigiaminja';

-- If empty, ensure shared_preload_libraries is set and PostgreSQL restarted
SHOW shared_preload_libraries;
```

### "COPY format 'jinja' not recognized"

This error means the extension wasn't loaded via `shared_preload_libraries`. Fix:

1. Add `shared_preload_libraries = 'pigiaminja'` to postgresql.conf
2. Restart PostgreSQL server
3. CREATE EXTENSION pigiaminja;

### Testing Issues

```bash
# Clean rebuild if tests fail
cargo clean
cargo pgrx install
cargo pgrx test pg15
```

## 📊 Test Results

All tests pass on supported PostgreSQL versions:

```
✅ PostgreSQL 14: 5 passed; 0 failed
✅ PostgreSQL 15: 5 passed; 0 failed  
✅ PostgreSQL 16: 5 passed; 0 failed
✅ PostgreSQL 17: 5 passed; 0 failed
```

## 📄 License

MIT License

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all PostgreSQL versions pass tests
5. Submit a pull request

## 📚 References

- [pgrx Documentation](https://github.com/pgcentralfoundation/pgrx)
- [PostgreSQL Extension Development](https://www.postgresql.org/docs/current/extend.html)
- [pg_parquet](https://github.com/CrunchyData/pg_parquet) - Reference implementation for COPY format extensions