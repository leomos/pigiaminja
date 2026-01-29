use std::ffi::{c_char, CStr};
use std::sync::Arc;

use minijinja::value::{Enumerator, Object};
use minijinja::{context, Environment, Value};
use pgrx::{
    pg_sys::{
        makeStringInfo, pfree, pq_beginmessage_reuse, pq_endmessage_reuse, resetStringInfo,
        slot_getallattrs, AsPgCStr, BlessTupleDesc, CommandDest, CurrentMemoryContext, Datum,
        DestReceiver, MemoryContext, StringInfoData, TupleDesc, TupleTableSlot,
    },
    prelude::*,
    AllocatedByPostgres, FromDatum, PgBox, PgMemoryContexts, PgTupleDesc,
};

use super::output::CopyDestination;

const TEMPLATE_NAME: &str = "row";

/// How to turn a column's datum into a minijinja value. Resolved once at startup
/// from the column's type OID so the per-row hot path performs no catalog lookups.
enum ColumnConv {
    Text,
    Int2,
    Int4,
    Int8,
    Float4,
    Float8,
    Bool,
    /// `json` (OID 114): a *text* varlena, parsed with `serde_json` directly.
    Json,
    /// `jsonb` (OID 3802): a *binary* varlena, decoded via the `jsonb_out`
    /// function. Decoding a `json` datum with this path (or vice versa) reads
    /// the wrong varlena layout and corrupts memory, so the two are distinct.
    Jsonb,
    /// Fallback for any other type: call the type's text output function, whose
    /// lookup (`getTypeOutputInfo` + `fmgr_info`) is done once and cached here.
    Output { flinfo: pg_sys::FmgrInfo },
}

/// A single output row exposed to the Jinja template as the `row` map (so the
/// template can reference `row.<column>`). Holds the converted cell values plus
/// the shared (Arc) column names. The per-row cost is one `Vec` + one `Arc`
/// allocation, instead of building a serde_json map and re-serialising it into a
/// minijinja value (two map representations) on every row.
#[derive(Debug)]
struct RowObject {
    names: Arc<Vec<Box<str>>>,
    values: Vec<Value>,
}

impl Object for RowObject {
    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        let key = key.as_str()?;
        let idx = self.names.iter().position(|name| name.as_ref() == key)?;
        Some(self.values[idx].clone())
    }

    fn enumerate(self: &Arc<Self>) -> Enumerator {
        let names = self.names.clone();
        Enumerator::Iter(Box::new(
            (0..names.len()).map(move |i| Value::from(names[i].as_ref())),
        ))
    }
}

#[repr(C)]
pub(crate) struct JinjaDestReceiver {
    dest: DestReceiver,
    natts: usize,
    tupledesc: TupleDesc,
    env: *mut Environment<'static>,
    template_string: *mut String,
    /// Where rendered rows go: stdout (wire protocol), a file, or a program's stdin.
    output_destination: *mut CopyDestination,
    memory_context: MemoryContext,
    /// Shared column names, Arc-cloned into each row (never re-allocated per row).
    column_names: *mut Arc<Vec<Box<str>>>,
    /// Per-column datum converters, resolved once at startup.
    column_convs: *mut Vec<ColumnConv>,
    /// Reusable StringInfo buffer for COPY data messages (avoids per-row allocation).
    copy_buf: *mut StringInfoData,
}

impl JinjaDestReceiver {
    fn process_tuple(&mut self, slot: *mut TupleTableSlot) {
        unsafe {
            // Extract all attributes from the slot
            slot_getallattrs(slot);

            let natts = self.natts;
            let datums = std::slice::from_raw_parts((*slot).tts_values, natts);
            let nulls = std::slice::from_raw_parts((*slot).tts_isnull, natts);

            let convs = &mut *self.column_convs;
            let names = &*self.column_names;

            let mut values = Vec::with_capacity(natts);
            for (idx, (datum, is_null)) in datums.iter().zip(nulls).enumerate() {
                values.push(if *is_null {
                    Value::from(())
                } else {
                    convert_datum(*datum, &mut convs[idx])
                });
            }

            let row = Value::from_object(RowObject {
                names: names.clone(),
                values,
            });

            // Use pre-compiled template instead of render_str (which recompiles per row)
            let env = self
                .env
                .as_ref()
                .expect("Jinja environment not initialized");

            let template = env
                .get_template(TEMPLATE_NAME)
                .expect("Pre-compiled template not found");

            // Render directly into the reused buffer. Avoids a per-row output
            // String allocation plus an extra full-row copy. For STDOUT the
            // buffer is the wire message itself, framed by the pq_*_reuse
            // calls; for file/program destinations it is scratch space whose
            // payload is handed to the CopyDestination.
            let destination = self
                .output_destination
                .as_mut()
                .expect("output destination not initialized");

            let buf = self.copy_buf;
            if destination.is_stdout() {
                pq_beginmessage_reuse(buf, b'd' as _);
            } else {
                resetStringInfo(buf);
            }
            if let Err(e) =
                template.render_to_write(context! { row => row }, StringInfoWriter(buf))
            {
                pgrx::error!("Failed to render Jinja template: {}", e);
            }
            if destination.is_stdout() {
                pq_endmessage_reuse(buf);
            } else {
                let data =
                    std::slice::from_raw_parts((*buf).data as *const u8, (*buf).len as usize);
                if let Err(e) = destination.write_data(data) {
                    pgrx::error!("Failed to write COPY data: {}", e);
                }
            }
        }
    }
}

/// `std::io::Write` adapter that appends bytes straight into a Postgres
/// `StringInfo`, letting the template render directly into the COPY send buffer.
struct StringInfoWriter(*mut StringInfoData);

impl std::io::Write for StringInfoWriter {
    #[inline]
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        unsafe {
            pg_sys::appendBinaryStringInfo(self.0, buf.as_ptr() as *const _, buf.len() as _);
        }
        Ok(buf.len())
    }

    #[inline]
    fn write_all(&mut self, buf: &[u8]) -> std::io::Result<()> {
        unsafe {
            pg_sys::appendBinaryStringInfo(self.0, buf.as_ptr() as *const _, buf.len() as _);
        }
        Ok(())
    }

    #[inline]
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// Convert a single non-null `datum` into a minijinja value using the
/// precomputed converter for its column.
///
/// # Safety
/// `datum` must be a valid datum of the column type `conv` was built for.
unsafe fn convert_datum(datum: Datum, conv: &mut ColumnConv) -> Value {
    match conv {
        // Borrow the text out of the datum (detoasted) and let minijinja inline
        // short strings as SmallStr; no intermediate String allocation.
        ColumnConv::Text => {
            <&str>::from_datum(datum, false).map_or(Value::from(()), |s| Value::from(s))
        }
        ColumnConv::Int2 => i16::from_datum(datum, false).map_or(Value::from(()), Value::from),
        ColumnConv::Int4 => i32::from_datum(datum, false).map_or(Value::from(()), Value::from),
        ColumnConv::Int8 => i64::from_datum(datum, false).map_or(Value::from(()), Value::from),
        ColumnConv::Float4 => f32::from_datum(datum, false).map_or(Value::from(()), |v| {
            if v.is_finite() {
                Value::from(v)
            } else {
                Value::from(())
            }
        }),
        ColumnConv::Float8 => f64::from_datum(datum, false).map_or(Value::from(()), |v| {
            if v.is_finite() {
                Value::from(v)
            } else {
                Value::from(())
            }
        }),
        ColumnConv::Bool => bool::from_datum(datum, false).map_or(Value::from(()), Value::from),
        ColumnConv::Json => {
            pgrx::Json::from_datum(datum, false).map_or(Value::from(()), |j| Value::from_serialize(j.0))
        }
        ColumnConv::Jsonb => {
            pgrx::JsonB::from_datum(datum, false).map_or(Value::from(()), |j| Value::from_serialize(j.0))
        }
        ColumnConv::Output { flinfo } => {
            let cstr = pg_sys::OutputFunctionCall(flinfo as *mut pg_sys::FmgrInfo, datum);
            if cstr.is_null() {
                Value::from(())
            } else {
                match CStr::from_ptr(cstr as *const c_char).to_str() {
                    Ok(s) => Value::from(s),
                    Err(_) => Value::from(()),
                }
            }
        }
    }
}

/// Resolve the converter for a column's type OID. For the fallback path this
/// looks up the type's output function once and caches it in `memory_context`.
///
/// # Safety
/// Must run inside a Postgres backend (performs catalog lookups).
unsafe fn column_conv_for(type_oid: u32, memory_context: MemoryContext) -> ColumnConv {
    match type_oid {
        // Text types: TEXTOID | VARCHAROID | BPCHAROID | NAMEOID
        25 | 1043 | 1042 | 19 => ColumnConv::Text,
        21 => ColumnConv::Int2,    // INT2OID
        23 => ColumnConv::Int4,    // INT4OID
        20 => ColumnConv::Int8,    // INT8OID
        700 => ColumnConv::Float4, // FLOAT4OID
        701 => ColumnConv::Float8, // FLOAT8OID
        16 => ColumnConv::Bool,    // BOOLOID
        114 => ColumnConv::Json,    // JSONOID (text varlena)
        3802 => ColumnConv::Jsonb,  // JSONBOID (binary varlena)
        _ => {
            // Cache the type's output function so the per-row path skips the
            // getTypeOutputInfo + fmgr_info catalog lookups entirely.
            let mut typoutput = pg_sys::Oid::INVALID;
            let mut typisvarlena = false;
            pg_sys::getTypeOutputInfo(
                pg_sys::Oid::from(type_oid),
                &mut typoutput,
                &mut typisvarlena,
            );
            let mut flinfo: pg_sys::FmgrInfo = std::mem::zeroed();
            pg_sys::fmgr_info_cxt(typoutput, &mut flinfo, memory_context);
            ColumnConv::Output { flinfo }
        }
    }
}

#[pg_guard]
pub(crate) extern "C-unwind" fn jinja_startup(
    dest: *mut DestReceiver,
    _operation: i32,
    tupledesc: TupleDesc,
) {
    let jinja_dest = unsafe {
        (dest as *mut JinjaDestReceiver)
            .as_mut()
            .expect("invalid jinja dest receiver ptr")
    };

    unsafe {
        // Store tuple descriptor
        jinja_dest.tupledesc = BlessTupleDesc(tupledesc);
        let tupledesc = PgTupleDesc::from_pg_unchecked(jinja_dest.tupledesc);
        jinja_dest.natts = tupledesc.len();

        // Cache per-column name + converter once. This pulls the type OID and
        // (for fallback types) the output function out of the per-row loop.
        let mut names = Vec::with_capacity(jinja_dest.natts);
        let mut convs = Vec::with_capacity(jinja_dest.natts);
        for idx in 0..jinja_dest.natts {
            let attribute = tupledesc.get(idx).expect("cannot get attribute");
            let type_oid: u32 = attribute.type_oid().value().into();
            names.push(attribute.name().to_string().into_boxed_str());
            convs.push(column_conv_for(type_oid, jinja_dest.memory_context));
        }
        jinja_dest.column_names = Box::into_raw(Box::new(Arc::new(names)));
        jinja_dest.column_convs = Box::into_raw(Box::new(convs));

        // Pre-allocate reusable StringInfo buffer for COPY data messages
        jinja_dest.copy_buf = makeStringInfo();

        // Initialize Jinja environment and pre-compile the template
        let mut ctx = PgMemoryContexts::For(jinja_dest.memory_context);
        ctx.switch_to(|_context| {
            let template_string = &*jinja_dest.template_string;
            let mut env = Environment::new();
            env.add_template_owned(TEMPLATE_NAME.to_owned(), template_string.clone())
                .unwrap_or_else(|e| pgrx::error!("Failed to compile Jinja template: {}", e));
            jinja_dest.env = Box::into_raw(Box::new(env));
        });
    }
}

#[pg_guard]
pub(crate) extern "C-unwind" fn jinja_receive(
    slot: *mut TupleTableSlot,
    dest: *mut DestReceiver,
) -> bool {
    let jinja_dest = unsafe {
        (dest as *mut JinjaDestReceiver)
            .as_mut()
            .expect("invalid jinja dest receiver ptr")
    };

    jinja_dest.process_tuple(slot);

    true
}

#[pg_guard]
pub(crate) extern "C-unwind" fn jinja_shutdown(dest: *mut DestReceiver) {
    let jinja_dest = unsafe {
        (dest as *mut JinjaDestReceiver)
            .as_mut()
            .expect("invalid jinja dest receiver ptr")
    };

    // Clean up allocated memory
    unsafe {
        if !jinja_dest.env.is_null() {
            let _ = Box::from_raw(jinja_dest.env);
            jinja_dest.env = std::ptr::null_mut();
        }

        if !jinja_dest.template_string.is_null() {
            let _ = Box::from_raw(jinja_dest.template_string);
            jinja_dest.template_string = std::ptr::null_mut();
        }

        if !jinja_dest.column_names.is_null() {
            let _ = Box::from_raw(jinja_dest.column_names);
            jinja_dest.column_names = std::ptr::null_mut();
        }

        if !jinja_dest.column_convs.is_null() {
            let _ = Box::from_raw(jinja_dest.column_convs);
            jinja_dest.column_convs = std::ptr::null_mut();
        }

        if !jinja_dest.copy_buf.is_null() {
            pfree((*jinja_dest.copy_buf).data as _);
            pfree(jinja_dest.copy_buf as _);
            jinja_dest.copy_buf = std::ptr::null_mut();
        }

        if !jinja_dest.output_destination.is_null() {
            let mut destination = Box::from_raw(jinja_dest.output_destination);
            if let Err(e) = destination.finalize() {
                pgrx::warning!("Failed to finalize output destination: {}", e);
            }
            jinja_dest.output_destination = std::ptr::null_mut();
        }
    }
}

#[pg_guard]
pub(crate) extern "C-unwind" fn jinja_destroy(_dest: *mut DestReceiver) {}

// Create a new JinjaDestReceiver
#[pg_guard]
pub(crate) extern "C-unwind" fn create_jinja_dest_receiver(
    template_content: *const c_char,
    output_destination: *mut CopyDestination,
) -> *mut JinjaDestReceiver {
    let memory_context = unsafe {
        pg_sys::AllocSetContextCreateExtended(
            CurrentMemoryContext as _,
            "Jinja Dest Receiver Context".as_pg_cstr(),
            pg_sys::ALLOCSET_DEFAULT_MINSIZE as _,
            pg_sys::ALLOCSET_DEFAULT_INITSIZE as _,
            pg_sys::ALLOCSET_DEFAULT_MAXSIZE as _,
        )
    };

    let mut jinja_dest = unsafe { PgBox::<JinjaDestReceiver, AllocatedByPostgres>::alloc0() };

    jinja_dest.dest.receiveSlot = Some(jinja_receive);
    jinja_dest.dest.rStartup = Some(jinja_startup);
    jinja_dest.dest.rShutdown = Some(jinja_shutdown);
    jinja_dest.dest.rDestroy = Some(jinja_destroy);
    jinja_dest.dest.mydest = CommandDest::DestCopyOut;

    // Convert template content from C string to Rust String
    let template_string = unsafe {
        CStr::from_ptr(template_content)
            .to_str()
            .expect("template content is not a valid C string")
            .to_string()
    };

    jinja_dest.tupledesc = std::ptr::null_mut();
    jinja_dest.natts = 0;
    jinja_dest.env = std::ptr::null_mut();
    jinja_dest.template_string = Box::into_raw(Box::new(template_string));
    jinja_dest.output_destination = output_destination;
    jinja_dest.memory_context = memory_context;
    jinja_dest.column_names = std::ptr::null_mut();
    jinja_dest.column_convs = std::ptr::null_mut();
    jinja_dest.copy_buf = std::ptr::null_mut();

    jinja_dest.into_pg()
}
