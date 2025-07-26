use std::ffi::{c_char, CStr};

use pgrx::pg_sys::{
    standard_ProcessUtility, DestReceiver, ParamListInfoData, PlannedStmt, ProcessUtility_hook,
    ProcessUtility_hook_type, QueryCompletion, QueryEnvironment,
};
use pgrx::{prelude::*, GucSetting};

pub static ENABLE_JINJA_COPY_HOOK: GucSetting<bool> = GucSetting::<bool>::new(true);

static mut PREV_PROCESS_UTILITY_HOOK: ProcessUtility_hook_type = None;

#[pg_guard]
#[no_mangle]
pub extern "C-unwind" fn init_jinja_copy_hook() {
    #[allow(static_mut_refs)]
    unsafe {
        if ProcessUtility_hook.is_some() {
            PREV_PROCESS_UTILITY_HOOK = ProcessUtility_hook
        }

        ProcessUtility_hook = Some(jinja_copy_hook);
    }
}

fn process_copy_to_jinja(
    p_stmt: &PgBox<PlannedStmt>,
    query_string: &CStr,
    read_only_tree: bool,
    dest: *mut DestReceiver,
    query_completion: *mut QueryCompletion,
) -> bool {
    // Import the functions from copy_to module
    use crate::copy_hook::copy_to::{execute_copy_to_jinja, is_copy_to_jinja_stmt};

    // Check if this is a COPY TO statement with jinja format
    if is_copy_to_jinja_stmt(p_stmt) {
        execute_copy_to_jinja(p_stmt, query_string, read_only_tree, dest, query_completion);
        return true;
    }

    false
}

#[pg_guard]
#[allow(clippy::too_many_arguments)]
extern "C-unwind" fn jinja_copy_hook(
    p_stmt: *mut PlannedStmt,
    query_string: *const c_char,
    read_only_tree: bool,
    context: u32,
    params: *mut ParamListInfoData,
    query_env: *mut QueryEnvironment,
    dest: *mut DestReceiver,
    query_completion: *mut QueryCompletion,
) {
    if !ENABLE_JINJA_COPY_HOOK.get() {
        call_prev_process_utility_hook(
            p_stmt,
            query_string,
            read_only_tree,
            context,
            params,
            query_env,
            dest,
            query_completion,
        );
        return;
    }

    let p_stmt = unsafe { PgBox::from_pg(p_stmt) };
    let query_string = unsafe { CStr::from_ptr(query_string) };

    let handled = process_copy_to_jinja(
        &p_stmt,
        query_string,
        read_only_tree,
        dest,
        query_completion,
    );

    if !handled {
        call_prev_process_utility_hook(
            p_stmt.as_ptr(),
            query_string.as_ptr(),
            read_only_tree,
            context,
            params,
            query_env,
            dest,
            query_completion,
        );
    }
}

fn call_prev_process_utility_hook(
    p_stmt: *mut PlannedStmt,
    query_string: *const c_char,
    read_only_tree: bool,
    context: u32,
    params: *mut ParamListInfoData,
    query_env: *mut QueryEnvironment,
    dest: *mut DestReceiver,
    query_completion: *mut QueryCompletion,
) {
    unsafe {
        if let Some(prev_hook) = PREV_PROCESS_UTILITY_HOOK {
            prev_hook(
                p_stmt,
                query_string,
                read_only_tree,
                context,
                params,
                query_env,
                dest,
                query_completion,
            );
        } else {
            standard_ProcessUtility(
                p_stmt,
                query_string,
                read_only_tree,
                context,
                params,
                query_env,
                dest,
                query_completion,
            );
        }
    }
}
