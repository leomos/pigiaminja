use std::ffi::CStr;

use pgrx::{
    is_a,
    pg_sys::{
        defGetString, makeStringInfo, pq_beginmessage, pq_endmessage, pq_putemptymessage,
        pq_sendbyte, pq_sendbytes, pq_sendint16, CommandTag, CopyStmt, DefElem, DestReceiver,
        NodeTag::T_CopyStmt, PlannedStmt, QueryCompletion,
    },
    prelude::*, PgBox, PgList
};

use super::hook::ENABLE_JINJA_COPY_HOOK;


/// Execute COPY TO with Jinja template formatting
pub(crate) fn execute_copy_to_jinja(
    p_stmt: &PgBox<PlannedStmt>,
    _query_string: &CStr,
    _read_only_tree: bool,
    dest: *mut DestReceiver,
    query_completion: *mut QueryCompletion,
) {
    let copy_stmt = unsafe { PgBox::<CopyStmt>::from_pg(p_stmt.utilityStmt as _) };
    
    // Determine if this is COPY TO STDOUT or to a file
    let is_to_stdout = copy_stmt.filename.is_null();
    
    unsafe {
        // For COPY TO STDOUT, send the placeholder data using PostgreSQL's copy protocol
        if is_to_stdout {
            // Send COPY begin message
            send_copy_begin(1, false); // 1 column, text format
            
            // Send the placeholder data
            let placeholder = "JINJA_EXTENTIONS_PLACEHOLDER\n";
            send_copy_data(placeholder.as_bytes());
            
            // Send COPY end message
            send_copy_end();
        } else {
            // For COPY TO file, use the standard PostgreSQL file writing mechanism
            // For now, just output a notice as a fallback
            pgrx::notice!("JINJA_EXTENTIONS_PLACEHOLDER");
        }
        
        // Set completion status
        if !query_completion.is_null() {
            let mut completion_tag = PgBox::from_pg(query_completion);
            completion_tag.nprocessed = 1;
            completion_tag.commandTag = CommandTag::CMDTAG_COPY;
        }
    }
}

/// Check if a COPY statement uses Jinja format
pub(crate) fn is_copy_to_jinja_stmt(p_stmt: &PgBox<PlannedStmt>) -> bool {
    // The GUC pigiaminja.enable_jinja_copy_hook must be set to true
    if !ENABLE_JINJA_COPY_HOOK.get() {
        return false;
    }

    let is_copy_stmt = unsafe { is_a(p_stmt.utilityStmt, T_CopyStmt) };

    if !is_copy_stmt {
        return false;
    }

    let copy_stmt = unsafe { PgBox::<CopyStmt>::from_pg(p_stmt.utilityStmt as _) };

    // Only handle COPY TO (not COPY FROM)
    if copy_stmt.is_from {
        return false;
    }

    // Check if format is jinja
    is_jinja_format_option(p_stmt)
}

/// Extract Jinja template from COPY statement options (placeholder for future expansion)
pub(crate) fn extract_jinja_template(_p_stmt: &PgBox<PlannedStmt>) -> Option<String> {
    // Placeholder implementation - in the future this could parse template options
    None
}

/// Get a COPY statement option by name
fn copy_stmt_get_option(p_stmt: &PgBox<PlannedStmt>, option_name: &str) -> PgBox<DefElem> {
    let copy_stmt = unsafe { PgBox::<CopyStmt>::from_pg(p_stmt.utilityStmt as _) };

    let copy_options = unsafe { PgList::<DefElem>::from_pg(copy_stmt.options) };

    for current_option in copy_options.iter_ptr() {
        let current_option = unsafe { PgBox::<DefElem>::from_pg(current_option) };

        let current_option_name = unsafe {
            CStr::from_ptr(current_option.defname)
                .to_str()
                .expect("copy option is not a valid CString")
        };

        if current_option_name == option_name {
            return current_option;
        }
    }

    PgBox::null()
}

/// Check if the COPY statement specifies FORMAT jinja
fn is_jinja_format_option(p_stmt: &PgBox<PlannedStmt>) -> bool {
    let format_option = copy_stmt_get_option(p_stmt, "format");

    if format_option.is_null() {
        return false;
    }

    let format = unsafe { defGetString(format_option.as_ptr()) };

    let format = unsafe {
        CStr::from_ptr(format)
            .to_str()
            .unwrap_or_else(|e| panic!("format option is not a valid CString: {e}"))
    };

    format == "jinja"
}


// Helper functions for PostgreSQL COPY protocol

unsafe fn send_copy_begin(natts: i16, is_binary: bool) {
    let buf = makeStringInfo();

    pq_beginmessage(buf, b'H' as _);

    let copy_format = if is_binary { 1 } else { 0 };
    pq_sendbyte(buf, copy_format); /* overall format */

    pq_sendint16(buf, natts as u16);
    for _ in 0..natts {
        /* use the same format for all columns */
        pq_sendint16(buf, copy_format as u16);
    }

    pq_endmessage(buf);
}

unsafe fn send_copy_end() {
    pq_putemptymessage(b'c' as _);
}

unsafe fn send_copy_data(data: &[u8]) {
    let buf = makeStringInfo();

    pq_beginmessage(buf, b'd' as _);
    pq_sendbytes(buf, data.as_ptr() as _, data.len() as _);
    pq_endmessage(buf);
}

