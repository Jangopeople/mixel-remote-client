// Read-only diagnostics executed inside the exact owned standard-user fixture.
// No token, privilege, DACL, named-object contents or readiness is changed.
#[cfg(windows)]
mod standard_namespace_readonly {
    use std::{ffi::c_void, io, ptr};
    #[repr(C)]
    struct UnicodeString { length: u16, maximum: u16, buffer: *const u16 }
    #[repr(C)]
    struct ObjectAttributes {
        length: u32, root: *mut c_void, name: *const UnicodeString,
        attributes: u32, security: *mut c_void, qos: *mut c_void,
    }
    type OpenObject = unsafe extern "system" fn(*mut *mut c_void, u32, *const ObjectAttributes) -> i32;
    type StatusError = unsafe extern "system" fn(i32) -> u32;
    #[link(name="kernel32")]
    extern "system" {
        fn GetModuleHandleW(name: *const u16) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const u8) -> *const c_void;
        fn GetCurrentProcessId() -> u32;
        fn ProcessIdToSessionId(pid: u32, session: *mut u32) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn GetLastError() -> u32;
    }
    fn native_failure() -> io::Error {
        // Capture immediately; no temporary UTF-16 allocation has ended.
        io::Error::from_raw_os_error(unsafe { GetLastError() } as i32)
    }
    fn object_error(path: &str, access: u32, open: OpenObject, status_error: StatusError) -> io::Result<u32> {
        if path.len() > 128 { return Err(io::Error::new(io::ErrorKind::InvalidInput,"Bounded namespace path exceeded")); }
        let name: Vec<u16> = path.encode_utf16().chain(Some(0)).collect();
        let text = UnicodeString {
            length: ((name.len()-1)*2) as u16, maximum: (name.len()*2) as u16, buffer: name.as_ptr(),
        };
        let attributes = ObjectAttributes {
            length: std::mem::size_of::<ObjectAttributes>() as u32, root: ptr::null_mut(), name: &text,
            attributes: 0x40, security: ptr::null_mut(), qos: ptr::null_mut(),
        };
        let mut handle = ptr::null_mut();
        let status = unsafe { open(&mut handle,access,&attributes) };
        if status < 0 {
            if !handle.is_null() {
                if unsafe { CloseHandle(handle) } == 0 { return Err(native_failure()); }
                return Err(io::Error::new(io::ErrorKind::InvalidData,"Failed diagnostic object open returned a handle"));
            }
            return Ok(unsafe { status_error(status) });
        }
        if handle.is_null() { return Err(io::Error::new(io::ErrorKind::InvalidData,"Successful diagnostic object open returned no handle")); }
        if unsafe { CloseHandle(handle) } == 0 { return Err(native_failure()); }
        Ok(0)
    }
    pub(super) fn observe(event: &str) -> io::Result<String> {
        // This is solely the exported disposable source fixture's exact GUID.
        // Reject arbitrary names before opening any native diagnostic object.
        let leaf = event.strip_prefix("Global\\").or_else(|| event.strip_prefix("Local\\")).unwrap_or(event);
        let guid = leaf.strip_prefix("Mixel-Remote-Standard-Lease-").unwrap_or("");
        if guid.len() != 32 || !guid.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput,"Expected an owned source-fixture GUID event"));
        }
        let mut session = 0;
        if unsafe { ProcessIdToSessionId(GetCurrentProcessId(),&mut session) } == 0 { return Err(native_failure()); }
        let name: Vec<u16> = "ntdll.dll".encode_utf16().chain(Some(0)).collect();
        let module = unsafe { GetModuleHandleW(name.as_ptr()) };
        if module.is_null() { return Err(native_failure()); }
        let open = unsafe { GetProcAddress(module,b"NtOpenDirectoryObject\0".as_ptr()) };
        if open.is_null() { return Err(native_failure()); }
        let error = unsafe { GetProcAddress(module,b"RtlNtStatusToDosError\0".as_ptr()) };
        if error.is_null() { return Err(native_failure()); }
        let event_open = unsafe { GetProcAddress(module,b"NtOpenEvent\0".as_ptr()) };
        if event_open.is_null() { return Err(native_failure()); }
        let open: OpenObject = unsafe { std::mem::transmute(open) };
        let event_open: OpenObject = unsafe { std::mem::transmute(event_open) };
        let error: StatusError = unsafe { std::mem::transmute(error) };
        let mut result = format!("{{\"pid\":{},\"session\":{}",std::process::id(),session);
        for (label,path) in [
            ("root","\\".to_owned()), ("sessions","\\Sessions".to_owned()),
            ("sessionRoot",format!("\\Sessions\\{session}")),
            ("sessionObjects",format!("\\Sessions\\{session}\\BaseNamedObjects")),
            ("globalObjects","\\BaseNamedObjects".to_owned()),
        ] {
            // Observe individual and combined specific rights separately. Mask15
            // is all four directory-specific rights, not DIRECTORY_ALL_ACCESS.
            for (right,access) in [
                ("TraverseError",2),("QueryError",1),("CreateAccessError",4),
                ("CreateSubdirectoryAccessError",8),("CombinedCreationAccessError",7),
                ("AllSpecificAccessError",15),
            ] {
                let code = object_error(&path,access,open,error)?;
                result.push_str(&format!(",\"{label}{right}\":{code}"));
            }
        }
        // A read-only native-path discriminator, never a substitute for the
        // actual Win32 guard result captured before this secondary observation.
        for (label,path) in [
            ("GlobalAbsoluteEventError",format!("\\BaseNamedObjects\\{leaf}")),
            ("SessionAbsoluteEventError",format!("\\Sessions\\{session}\\BaseNamedObjects\\{leaf}")),
        ] {
            let code = object_error(&path,0x00100000,event_open,error)?;
            result.push_str(&format!(",\"{label}\":{code}"));
        }
        result.push('}');
        Ok(result)
    }
}
