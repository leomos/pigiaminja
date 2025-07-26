use std::ffi::CStr;

use copy_hook::hook::{init_jinja_copy_hook, ENABLE_JINJA_COPY_HOOK};
use pgrx::pg_sys::AsPgCStr;
use pgrx::{prelude::*, GucContext, GucFlags, GucRegistry};

mod copy_hook;

#[cfg(any(test, feature = "pg_test"))]
mod pgrx_tests;

pgrx::pg_module_magic!();

#[pg_guard]
pub extern "C-unwind" fn _PG_init() {
    unsafe {
        GucRegistry::define_bool_guc(
            CStr::from_ptr("pigiaminja.enable_copy_hooks".as_pg_cstr()),
            CStr::from_ptr("Enable Jinja template copy hooks".as_pg_cstr()),
            CStr::from_ptr("Enable Jinja template copy hooks for COPY TO command".as_pg_cstr()),
            &ENABLE_JINJA_COPY_HOOK,
            GucContext::Userset,
            GucFlags::default(),
        )
    };

    init_jinja_copy_hook();
}

/// This module is required by `cargo pgrx test` invocations.
/// It must be visible at the root of your extension crate.
#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {
        // perform one-off initialization when the pg_test framework starts
    }

    pub fn postgresql_conf_options() -> Vec<&'static str> {
        // return any postgresql.conf settings that are required for your tests
        vec!["shared_preload_libraries = 'pigiaminja'"]
    }
}
