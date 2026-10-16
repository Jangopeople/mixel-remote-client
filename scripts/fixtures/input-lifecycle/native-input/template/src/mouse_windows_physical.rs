//! Exact Windows branch of new physical mouse helper; live OS bits mocked only.
use std::sync::atomic::{AtomicU16,Ordering};
static ASYNC:AtomicU16=AtomicU16::new(0);
mod winapi {pub mod um {#[allow(non_snake_case)]pub mod winuser {
 pub const VK_LMENU:i32=0;pub const VK_RMENU:i32=1;pub const VK_LCONTROL:i32=2;pub const VK_RCONTROL:i32=3;pub const VK_LSHIFT:i32=4;pub const VK_RSHIFT:i32=5;pub const VK_LWIN:i32=6;pub const VK_RWIN:i32=7;
 pub unsafe fn GetAsyncKeyState(key:i32)->i16 {if super::super::super::ASYNC.load(std::sync::atomic::Ordering::SeqCst)&(1<<key)!=0 {i16::MIN}else{0}}
}}}
include!("mouse_windows_physical_selected.rs");
#[test]fn actual_windows_mouse_reads_live_both_sides_preserves_flags_and_forgets_released_bit() {
 for (bit,want) in [(0,(true,false,false,false)),(1,(true,false,false,false)),(2,(false,true,false,false)),(3,(false,true,false,false)),(4,(false,false,true,false)),(5,(false,false,true,false)),(6,(false,false,false,true)),(7,(false,false,false,true))] {ASYNC.store(1<<bit,Ordering::SeqCst);assert_eq!(get_physical_modifiers_state(false,false,false,false),want);ASYNC.store(0,Ordering::SeqCst);assert_eq!(get_physical_modifiers_state(false,false,false,false),(false,false,false,false));}
 assert_eq!(get_physical_modifiers_state(true,true,true,true),(true,true,true,true));
}
