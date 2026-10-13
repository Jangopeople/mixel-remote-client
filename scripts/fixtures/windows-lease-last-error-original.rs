    fn create() -> std::io::Result<Lease> {
        // Every interactive owner can hold SYNCHRONIZE access; only SYSTEM and
        // administrators can mutate its ACL. Event state is irrelevant: its
        // kernel object's existence lasts until the last process handle exits.
        let sddl = wide("D:P(A;;0x00100000;;;WD)(A;;GA;;;SY)(A;;GA;;;BA)");
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        let attributes = SecurityAttributes {
            length: std::mem::size_of::<SecurityAttributes>() as u32,
            descriptor,
            inherit: 0,
        };
        let handle = unsafe { CreateEventExW(&attributes, wide(EVENT).as_ptr(), 0, 0x00100000) };
        let error = if handle.is_null() {
            Some(std::io::Error::last_os_error())
        } else {
            None
        };
        unsafe {
            LocalFree(descriptor);
        }
        if let Some(error) = error {
            return Err(error);
        }
        Ok(Lease(handle as usize))
    }

    fn probe() -> std::io::Result<bool> {
        let handle = unsafe { OpenEventW(0x00100000, 0, wide(EVENT).as_ptr()) };
        if handle.is_null() {
            return if unsafe { GetLastError() } == 2 {
                Ok(false)
            } else {
                Err(std::io::Error::last_os_error())
            };
        }
        unsafe {
            CloseHandle(handle);
        }
        Ok(true)
    }
