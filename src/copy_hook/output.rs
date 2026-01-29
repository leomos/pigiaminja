use std::ffi::{c_char, CStr};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};

use pgrx::pg_sys::errcodes::PgSqlErrorCode;
use pgrx::pg_sys::{
    ereport, has_privs_of_role, makeStringInfo, pq_beginmessage, pq_endmessage, pq_sendbytes,
    GetUserId, Oid, ROLE_PG_EXECUTE_SERVER_PROGRAM, ROLE_PG_WRITE_SERVER_FILES,
};

/// Same permission checks DoCopy applies: writing a server-side file or
/// piping through a program is reserved to superusers and the built-in
/// pg_write_server_files / pg_execute_server_program roles.
fn check_destination_privilege(is_program: bool) {
    let (role_oid, role_name, action) = if is_program {
        (
            ROLE_PG_EXECUTE_SERVER_PROGRAM,
            "pg_execute_server_program",
            "COPY to or from an external program",
        )
    } else {
        (
            ROLE_PG_WRITE_SERVER_FILES,
            "pg_write_server_files",
            "COPY to a file",
        )
    };

    let has_privilege = unsafe { has_privs_of_role(GetUserId(), Oid::from(role_oid)) };
    if !has_privilege {
        ereport!(
            ERROR,
            PgSqlErrorCode::ERRCODE_INSUFFICIENT_PRIVILEGE,
            format!("permission denied to {}", action),
            format!(
                "Only roles with privileges of the \"{}\" role may {}.",
                role_name, action
            )
        );
    }
}

/// Represents the destination for COPY TO output
pub enum CopyDestination {
    Stdout,
    File(BufWriter<File>),
    Program(Child),  // Hold Child to access stdin and wait on drop
}

impl CopyDestination {
    /// Create a CopyDestination from COPY statement parameters
    pub fn from_copy_stmt(filename: *mut c_char, is_program: bool) -> Result<Self, String> {
        unsafe {
            // Check for COPY TO PROGRAM
            if is_program {
                check_destination_privilege(true);

                let command_str = CStr::from_ptr(filename)
                    .to_str()
                    .map_err(|e| format!("Invalid command: {}", e))?;

                let child = Command::new("sh")
                    .arg("-c")
                    .arg(command_str)
                    .stdin(Stdio::piped())
                    .spawn()
                    .map_err(|e| format!("Failed to spawn program '{}': {}", command_str, e))?;

                return Ok(CopyDestination::Program(child));
            }

            // Check if filename is null -> STDOUT
            if filename.is_null() {
                return Ok(CopyDestination::Stdout);
            }

            // Otherwise, it's a file path
            check_destination_privilege(false);

            let filename_str = CStr::from_ptr(filename)
                .to_str()
                .map_err(|e| format!("Invalid filename: {}", e))?;

            let path = Path::new(filename_str);
            let file = File::create(path)
                .map_err(|e| format!("Failed to create file '{}': {}", filename_str, e))?;

            Ok(CopyDestination::File(BufWriter::new(file)))
        }
    }

    /// Write data to the destination
    pub fn write_data(&mut self, data: &[u8]) -> Result<(), String> {
        match self {
            CopyDestination::Stdout => {
                // Use PostgreSQL wire protocol for stdout
                unsafe {
                    let buf = makeStringInfo();
                    pq_beginmessage(buf, b'd' as _);
                    pq_sendbytes(buf, data.as_ptr() as _, data.len() as _);
                    pq_endmessage(buf);
                }
                Ok(())
            }
            CopyDestination::File(writer) => {
                writer
                    .write_all(data)
                    .map_err(|e| format!("Failed to write to file: {}", e))
            }
            CopyDestination::Program(child) => {
                if let Some(stdin) = child.stdin.as_mut() {
                    stdin
                        .write_all(data)
                        .map_err(|e| format!("Failed to write to program: {}", e))
                } else {
                    Err("Program stdin not available".to_string())
                }
            }
        }
    }

    /// Check if destination is stdout
    pub fn is_stdout(&self) -> bool {
        matches!(self, CopyDestination::Stdout)
    }

    /// Finalize the destination (flush buffers, etc.)
    pub fn finalize(&mut self) -> Result<(), String> {
        match self {
            CopyDestination::Stdout => Ok(()),
            CopyDestination::File(writer) => writer
                .flush()
                .map_err(|e| format!("Failed to flush file: {}", e)),
            CopyDestination::Program(child) => {
                // Drop stdin to signal EOF
                drop(child.stdin.take());
                // Wait for child to exit
                child
                    .wait()
                    .map_err(|e| format!("Failed to wait for program: {}", e))?;
                Ok(())
            }
        }
    }
}
