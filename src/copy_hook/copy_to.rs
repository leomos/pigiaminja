use std::ffi::{CStr, CString};

use pgrx::{
    is_a,
    pg_sys::{
        defGetString, makeStringInfo, pg_plan_query, pq_beginmessage, pq_endmessage,
        pq_putemptymessage, pq_sendbyte, pq_sendint16, A_Star, ColumnRef, CommandTag, CopyStmt,
        CreateNewPortal, DefElem, DestReceiver, GetActiveSnapshot, Node,
        NodeTag::{self, T_CopyStmt},
        ParamListInfoData, PlannedStmt, PortalDefineQuery, PortalDrop, PortalRun, PortalStart,
        QueryCompletion, QueryEnvironment, RangeVar, RawStmt, ResTarget, SelectStmt,
        CURSOR_OPT_PARALLEL_OK,
    },
    AllocatedByRust, PgBox, PgList,
};

use super::dest_receiver::create_jinja_dest_receiver;
use super::hook::ENABLE_JINJA_COPY_HOOK;
use super::pg_compat::pg_analyze_and_rewrite;

/// Execute COPY TO with Jinja template formatting using DestReceiver pattern
pub(crate) fn execute_copy_to_jinja(
    p_stmt: &PgBox<PlannedStmt>,
    query_string: &CStr,
    _read_only_tree: bool,
    _dest: *mut DestReceiver,
    query_completion: *mut QueryCompletion,
) {
    unsafe {
        let _copy_stmt = PgBox::<CopyStmt>::from_pg(p_stmt.utilityStmt as _);

        // Extract the template content from the COPY statement
        let template_content = extract_jinja_template(p_stmt)
            .unwrap_or_else(|| pgrx::error!("template option is required for jinja format"));

        let template_content_cstr =
            CString::new(template_content).expect("Failed to create CString from template content");

        // Create custom Jinja DestReceiver
        let jinja_dest = create_jinja_dest_receiver(template_content_cstr.as_ptr());

        // Prepare parameters - create from null pointers
        let params = PgBox::<ParamListInfoData>::from_pg(std::ptr::null_mut());
        let query_env = PgBox::<QueryEnvironment>::from_pg(std::ptr::null_mut());

        // Execute with our custom DestReceiver
        let processed = execute_copy_to_with_dest_receiver(
            p_stmt,
            query_string,
            &params,
            &query_env,
            &PgBox::from_pg(jinja_dest as *mut DestReceiver),
        );

        // Set completion status
        if !query_completion.is_null() {
            let mut completion_tag = PgBox::from_pg(query_completion);
            completion_tag.nprocessed = processed as u64;
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
    let is_jinja = is_jinja_format_option(p_stmt);

    // If format is jinja, template option is mandatory
    if is_jinja {
        let template_option = copy_stmt_get_option(p_stmt, "template");
        if template_option.is_null() {
            pgrx::error!("template option is required when using jinja format");
        }
    }

    is_jinja
}

/// Extract Jinja template content from COPY statement options
pub(crate) fn extract_jinja_template(p_stmt: &PgBox<PlannedStmt>) -> Option<String> {
    let template_option = copy_stmt_get_option(p_stmt, "template");

    if template_option.is_null() {
        return None;
    }

    let template_content = unsafe { defGetString(template_option.as_ptr()) };

    let template_content = unsafe {
        CStr::from_ptr(template_content)
            .to_str()
            .unwrap_or_else(|e| panic!("template option is not a valid CString: {e}"))
    };

    Some(template_content.to_string())
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

// Execute COPY TO with our custom DestReceiver (based on pg_parquet implementation)
fn execute_copy_to_with_dest_receiver(
    p_stmt: &PgBox<PlannedStmt>,
    query_string: &CStr,
    params: &PgBox<ParamListInfoData>,
    query_env: &PgBox<QueryEnvironment>,
    jinja_dest: &PgBox<DestReceiver>,
) -> i64 {
    unsafe {
        let copy_stmt = PgBox::<CopyStmt>::from_pg(p_stmt.utilityStmt as _);

        // Check if this is a relation-based COPY or a query-based COPY
        let raw_query = if copy_stmt.relation.is_null() {
            // COPY (SELECT ...) TO
            let mut raw_query = PgBox::<RawStmt, AllocatedByRust>::alloc_node(NodeTag::T_RawStmt);
            raw_query.stmt_location = p_stmt.stmt_location;
            raw_query.stmt_len = p_stmt.stmt_len;
            raw_query.stmt = copy_stmt.query;
            raw_query
        } else {
            // COPY table TO
            let relation = PgBox::from_pg(copy_stmt.relation);
            let select_stmt = convert_copy_to_relation_to_select_stmt(&copy_stmt, &relation);

            let mut raw_query = PgBox::<RawStmt, AllocatedByRust>::alloc_node(NodeTag::T_RawStmt);
            raw_query.stmt_location = p_stmt.stmt_location;
            raw_query.stmt_len = p_stmt.stmt_len;
            raw_query.stmt = select_stmt.into_pg() as _;
            raw_query
        };

        // Send COPY begin message
        send_copy_begin(1, false); // 1 column, text format

        // Analyze and rewrite the query
        let rewritten_queries = pg_analyze_and_rewrite(
            raw_query.as_ptr(),
            query_string.as_ptr(),
            query_env.as_ptr(),
        );

        let query = PgList::from_pg(rewritten_queries)
            .pop()
            .expect("rewritten query is empty");

        // Plan the query
        let plan = pg_plan_query(
            query,
            std::ptr::null(),
            CURSOR_OPT_PARALLEL_OK as _,
            params.as_ptr(),
        );

        // Create a portal for the query
        let portal = CreateNewPortal();
        let mut portal = PgBox::from_pg(portal);
        portal.visible = false;

        let mut plans = PgList::<PlannedStmt>::new();
        plans.push(plan);

        PortalDefineQuery(
            portal.as_ptr(),
            std::ptr::null(),
            query_string.as_ptr(),
            CommandTag::CMDTAG_COPY,
            plans.as_ptr(),
            std::ptr::null_mut(),
        );

        PortalStart(portal.as_ptr(), params.as_ptr(), 0, GetActiveSnapshot());

        let mut completion_tag = QueryCompletion {
            commandTag: CommandTag::CMDTAG_COPY,
            nprocessed: 0,
        };

        // Execute the query with our custom DestReceiver
        PortalRun(
            portal.as_ptr(),
            i64::MAX,
            false,
            true,
            jinja_dest.as_ptr(),
            jinja_dest.as_ptr(),
            &mut completion_tag as _,
        );

        // Send COPY end message
        send_copy_end();

        PortalDrop(portal.as_ptr(), false);

        completion_tag.nprocessed as i64
    }
}

// Convert COPY table TO ... to SELECT * FROM table
fn convert_copy_to_relation_to_select_stmt(
    copy_stmt: &PgBox<CopyStmt>,
    relation: &PgBox<RangeVar>,
) -> PgBox<SelectStmt, AllocatedByRust> {
    unsafe {
        let mut target_list = PgList::new();

        if copy_stmt.attlist.is_null() {
            // SELECT * FROM relation
            let mut col_ref = PgBox::<ColumnRef, AllocatedByRust>::alloc_node(NodeTag::T_ColumnRef);
            let a_star = PgBox::<A_Star, AllocatedByRust>::alloc_node(NodeTag::T_A_Star);

            let mut field_list = PgList::new();
            field_list.push(a_star.into_pg());

            col_ref.fields = field_list.into_pg();
            col_ref.location = -1;

            let mut target = PgBox::<ResTarget, AllocatedByRust>::alloc_node(NodeTag::T_ResTarget);
            target.name = std::ptr::null_mut();
            target.indirection = std::ptr::null_mut();
            target.val = col_ref.into_pg() as _;
            target.location = -1;

            target_list.push(target.into_pg());
        } else {
            // SELECT a,b,... FROM relation
            let attribute_name_list = PgList::<Node>::from_pg(copy_stmt.attlist);
            for attribute_name in attribute_name_list.iter_ptr() {
                let mut col_ref =
                    PgBox::<ColumnRef, AllocatedByRust>::alloc_node(NodeTag::T_ColumnRef);

                let mut field_list = PgList::new();
                field_list.push(attribute_name);

                col_ref.fields = field_list.into_pg();
                col_ref.location = -1;

                let mut target =
                    PgBox::<ResTarget, AllocatedByRust>::alloc_node(NodeTag::T_ResTarget);
                target.name = std::ptr::null_mut();
                target.indirection = std::ptr::null_mut();
                target.val = col_ref.into_pg() as _;
                target.location = -1;

                target_list.push(target.into_pg());
            }
        }

        let mut select_stmt =
            PgBox::<SelectStmt, AllocatedByRust>::alloc_node(NodeTag::T_SelectStmt);
        select_stmt.targetList = target_list.into_pg();

        let mut from_list = PgList::new();
        from_list.push(relation.as_ptr() as *mut _);
        select_stmt.fromClause = from_list.into_pg();

        select_stmt
    }
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
