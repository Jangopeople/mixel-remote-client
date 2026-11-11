//! Java interface of the host core for the Android app (`ch.mixel.quicksupport.Native`).
#![allow(unsafe_code)]
use crate::{Config, Enrollment, Host};
use jni::{
    JNIEnv,
    objects::{JByteArray, JByteBuffer, JClass, JObject, JString, JValue},
    sys::{jboolean, jbyteArray, jint, jlong, jstring},
};

fn host<'a>(handle: jlong) -> Option<&'a Host> {
    // Safety: the handle is the pointer returned by `create` and stays valid until `destroy`.
    unsafe { (handle as *const Host).as_ref() }
}
fn fail(env: &mut JNIEnv, message: &str) {
    let _ = env.throw_new("java/lang/RuntimeException", message);
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_create<'local>(mut env: JNIEnv<'local>, _class: JClass<'local>, seed: JByteArray<'local>, listener: JObject<'local>) -> jlong {
    let seed: Option<[u8; 32]> = if seed.is_null() { None } else { env.convert_byte_array(&seed).ok().and_then(|bytes| bytes.try_into().ok()) };
    let (Ok(vm), Ok(listener)) = (env.get_java_vm(), env.new_global_ref(listener)) else {
        fail(&mut env, "Cannot keep the listener");
        return 0;
    };
    let created = Host::new(Config::production(), seed, move |event| {
        // Events come from the host core's own threads: attach them to Java once and keep them attached.
        if let Ok(mut env) = vm.attach_current_thread_as_daemon()
            && let Ok(json) = env.new_string(event.json())
        {
            let _ = env.call_method(&listener, "onEvent", "(Ljava/lang/String;)V", &[JValue::Object(&json)]);
            let _ = env.exception_clear();
            let _ = env.delete_local_ref(json);
        }
    });
    match created {
        Ok(host) => Box::into_raw(Box::new(host)) as jlong,
        Err(error) => {
            fail(&mut env, &error);
            0
        }
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_seed<'local>(env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong) -> jbyteArray {
    match host(handle).and_then(|host| env.byte_array_from_slice(&host.seed()).ok()) {
        Some(bytes) => bytes.into_raw(),
        None => std::ptr::null_mut(),
    }
}
/// Blocks; call off the main thread. Returns the registration as JSON, or throws.
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_enroll<'local>(mut env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong, code: JString<'local>, name: JString<'local>) -> jstring {
    let (Some(host), Ok(code), Ok(name)) = (host(handle), env.get_string(&code).map(String::from), env.get_string(&name).map(String::from)) else {
        fail(&mut env, "Invalid request");
        return std::ptr::null_mut();
    };
    match host.enroll(&code, &name).and_then(|enrollment| serde_json::to_string(&enrollment).map_err(|_| "Cannot store the registration".to_owned())) {
        Ok(json) => env.new_string(json).map(JString::into_raw).unwrap_or(std::ptr::null_mut()),
        Err(error) => {
            fail(&mut env, &error);
            std::ptr::null_mut()
        }
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_start<'local>(mut env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong, enrollment: JString<'local>, width: jint, height: jint, control: jboolean) {
    let result = host(handle).ok_or_else(|| "No host".to_owned()).and_then(|host| {
        let json: String = env.get_string(&enrollment).map(String::from).map_err(|_| "Invalid registration".to_owned())?;
        let enrollment: Enrollment = serde_json::from_str(&json).map_err(|_| "Invalid registration".to_owned())?;
        host.start(enrollment, width.max(0) as u32, height.max(0) as u32, control != 0)
    });
    if let Err(error) = result {
        fail(&mut env, &error);
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_decide<'local>(_env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong, accept: jboolean) {
    if let Some(host) = host(handle) {
        host.decide(accept != 0);
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_endSession<'local>(_env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong) {
    if let Some(host) = host(handle) {
        host.end_session();
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_stop<'local>(_env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong) {
    if let Some(host) = host(handle) {
        host.stop();
    }
}
/// `buffer` is a direct buffer from the video encoder; `offset` and `length` mark the picture in it.
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_pushFrame<'local>(env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong, buffer: JByteBuffer<'local>, offset: jint, length: jint) {
    let (Some(host), Ok(address), Ok(capacity)) = (host(handle), env.get_direct_buffer_address(&buffer), env.get_direct_buffer_capacity(&buffer)) else { return };
    let (offset, length) = (offset.max(0) as usize, length.max(0) as usize);
    if !address.is_null() && offset + length <= capacity {
        // Safety: the range lies inside the direct buffer, which Java keeps alive during this call.
        host.push_frame(unsafe { std::slice::from_raw_parts(address.add(offset), length) });
    }
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ch_mixel_quicksupport_Native_destroy<'local>(_env: JNIEnv<'local>, _class: JClass<'local>, handle: jlong) {
    if handle != 0 {
        // Safety: the handle is the pointer returned by `create` and is not used afterwards.
        let host = unsafe { Box::from_raw(handle as *mut Host) };
        host.stop();
        drop(host);
    }
}
