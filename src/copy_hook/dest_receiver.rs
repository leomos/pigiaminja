use std::ffi::{c_char, CStr};
use std::fs;

use minijinja::{context, Environment};
use pgrx::{
    prelude::*,
    pg_sys::{
        slot_getallattrs, AsPgCStr, BlessTupleDesc, CommandDest, CurrentMemoryContext, Datum,
        DestReceiver, MemoryContext, TupleDesc, TupleTableSlot,
        makeStringInfo, pq_beginmessage, pq_endmessage, pq_sendbytes,
    },
    AllocatedByPostgres, FromDatum, PgBox, PgMemoryContexts, PgTupleDesc,
};
use serde_json::{Map, Value as JsonValue};

#[repr(C)]
pub(crate) struct JinjaDestReceiver {
    dest: DestReceiver,
    template_path: *const c_char,
    natts: usize,
    tupledesc: TupleDesc,
    env: *mut Environment<'static>,
    template_content: *mut String,
    memory_context: MemoryContext,
}

impl JinjaDestReceiver {
    fn process_tuple(&mut self, slot: *mut TupleTableSlot) {
        unsafe {
            // Extract all attributes from the slot
            slot_getallattrs(slot);

            let natts = self.natts;
            let datums = std::slice::from_raw_parts((*slot).tts_values, natts);
            let nulls = std::slice::from_raw_parts((*slot).tts_isnull, natts);
            
            let tupledesc = PgTupleDesc::from_pg_unchecked(self.tupledesc);
            
            // Create a dictionary from the row data
            let mut row_dict = Map::new();
            
            for (idx, (datum, is_null)) in datums.iter().zip(nulls).enumerate() {
                let attribute = tupledesc.get(idx).expect("cannot get attribute");
                let attr_name = attribute.name();
                
                if *is_null {
                    row_dict.insert(attr_name.to_string(), JsonValue::Null);
                } else {
                    // Convert datum to appropriate JSON value based on type
                    let value = self.datum_to_json_value(*datum, attribute.type_oid().value().into());
                    row_dict.insert(attr_name.to_string(), value);
                }
            }
            
            // Render the template with the row data
            let env = self.env.as_ref().expect("Jinja environment not initialized");
            let template_content = self.template_content.as_ref().expect("Template content not loaded");
            
            match env.render_str(template_content, context! { row => row_dict }) {
                Ok(rendered) => self.send_copy_data(rendered.as_bytes()),
                Err(e) => pgrx::error!("Failed to render Jinja template: {}", e),
            }
        }
    }
    
    fn datum_to_json_value(&self, datum: Datum, type_oid: u32) -> JsonValue {
        unsafe {
            match type_oid {
                // Text types
                25 | 1043 | 1042 | 19 => { // TEXTOID | VARCHAROID | BPCHAROID | NAMEOID
                    if let Some(text) = String::from_datum(datum, false) {
                        JsonValue::String(text)
                    } else {
                        JsonValue::Null
                    }
                }
                // Integer types
                21 => { // INT2OID
                    if let Some(val) = i16::from_datum(datum, false) {
                        JsonValue::Number(val.into())
                    } else {
                        JsonValue::Null
                    }
                }
                23 => { // INT4OID
                    if let Some(val) = i32::from_datum(datum, false) {
                        JsonValue::Number(val.into())
                    } else {
                        JsonValue::Null
                    }
                }
                20 => { // INT8OID
                    if let Some(val) = i64::from_datum(datum, false) {
                        JsonValue::Number(val.into())
                    } else {
                        JsonValue::Null
                    }
                }
                // Float types
                700 => { // FLOAT4OID
                    if let Some(val) = f32::from_datum(datum, false) {
                        serde_json::Number::from_f64(val as f64)
                            .map(JsonValue::Number)
                            .unwrap_or(JsonValue::Null)
                    } else {
                        JsonValue::Null
                    }
                }
                701 => { // FLOAT8OID
                    if let Some(val) = f64::from_datum(datum, false) {
                        serde_json::Number::from_f64(val)
                            .map(JsonValue::Number)
                            .unwrap_or(JsonValue::Null)
                    } else {
                        JsonValue::Null
                    }
                }
                // Boolean
                16 => { // BOOLOID
                    if let Some(val) = bool::from_datum(datum, false) {
                        JsonValue::Bool(val)
                    } else {
                        JsonValue::Null
                    }
                }
                // JSON/JSONB
                114 | 3802 => { // JSONOID | JSONBOID
                    if let Some(json) = pgrx::JsonB::from_datum(datum, false) {
                        json.0
                    } else {
                        JsonValue::Null
                    }
                }
                // Default: convert to text
                _ => {
                    // Use PostgreSQL's output function to convert to text
                    if let Ok(text) = datum_to_text(datum, type_oid) {
                        JsonValue::String(text)
                    } else {
                        JsonValue::Null
                    }
                }
            }
        }
    }
    
    unsafe fn send_copy_data(&self, data: &[u8]) {
        let buf = makeStringInfo();
        pq_beginmessage(buf, b'd' as _);
        pq_sendbytes(buf, data.as_ptr() as _, data.len() as _);
        pq_endmessage(buf);
    }
}

// Helper function to convert datum to text using PostgreSQL's output function
unsafe fn datum_to_text(datum: Datum, type_oid: u32) -> Result<String, &'static str> {
    let mut typoutput: pg_sys::Oid = pg_sys::Oid::INVALID;
    let mut typvarlena: bool = false;
    
    pg_sys::getTypeOutputInfo(
        pg_sys::Oid::from(type_oid),
        &mut typoutput,
        &mut typvarlena,
    );
    
    if typoutput == pg_sys::Oid::INVALID {
        return Err("Invalid output function");
    }
    
    let result = pg_sys::OidOutputFunctionCall(typoutput, datum);
    
    CStr::from_ptr(result as *const c_char)
        .to_str()
        .map(|s| s.to_string())
        .map_err(|_| "Failed to convert to UTF-8")
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
        
        // Load template content
        let template_path = CStr::from_ptr(jinja_dest.template_path)
            .to_str()
            .expect("template path is not a valid C string");
            
        let template_content = fs::read_to_string(template_path)
            .unwrap_or_else(|e| pgrx::error!("Failed to read template file '{}': {}", template_path, e));
        
        // Initialize Jinja environment
        let mut ctx = PgMemoryContexts::For(jinja_dest.memory_context);
        ctx.switch_to(|_context| {
            let env = Box::new(Environment::new());
            let template_content = Box::new(template_content);
            
            jinja_dest.env = Box::into_raw(env);
            jinja_dest.template_content = Box::into_raw(template_content);
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
        
        if !jinja_dest.template_content.is_null() {
            let _ = Box::from_raw(jinja_dest.template_content);
            jinja_dest.template_content = std::ptr::null_mut();
        }
    }
}

#[pg_guard]
pub(crate) extern "C-unwind" fn jinja_destroy(_dest: *mut DestReceiver) {}

// Create a new JinjaDestReceiver
#[pg_guard]
pub(crate) extern "C-unwind" fn create_jinja_dest_receiver(
    template_path: *const c_char,
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
    
    let mut jinja_dest =
        unsafe { PgBox::<JinjaDestReceiver, AllocatedByPostgres>::alloc0() };
    
    jinja_dest.dest.receiveSlot = Some(jinja_receive);
    jinja_dest.dest.rStartup = Some(jinja_startup);
    jinja_dest.dest.rShutdown = Some(jinja_shutdown);
    jinja_dest.dest.rDestroy = Some(jinja_destroy);
    jinja_dest.dest.mydest = CommandDest::DestCopyOut;
    
    jinja_dest.template_path = template_path;
    jinja_dest.tupledesc = std::ptr::null_mut();
    jinja_dest.natts = 0;
    jinja_dest.env = std::ptr::null_mut();
    jinja_dest.template_content = std::ptr::null_mut();
    jinja_dest.memory_context = memory_context;
    
    jinja_dest.into_pg()
}