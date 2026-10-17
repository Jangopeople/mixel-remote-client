#!/usr/bin/env python3
"""Exercise actual pinned clipboard C cleanup and Rust error-Box Drop."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("clipboard_windows_patch", ROOT / "scripts/patch-support-windows-clipboard.py")
patcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)


def function(source, name):
    matches = list(re.finditer(r"^(?:static )?(?:BOOL|void|DWORD WINAPI) " + re.escape(name) + r"\([^;]*?\)\n\{", source, re.M))
    assert len(matches) == 1, name
    start = matches[0].start()
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#include <objidl.h>
#include <shlobj.h>
#else
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
typedef int BOOL;
typedef uint32_t ULONG, DWORD;
typedef int32_t LONG;
typedef uint16_t WCHAR;
typedef void *HWND, *HANDLE, *HMODULE, *LPDATAOBJECT, *LPVOID;
typedef unsigned int UINT, *PUINT;
typedef struct {int unused;} FILEDESCRIPTORW, MSG;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define INFINITE 0xffffffff
#endif
#include "cliprdr.h"
#ifndef WM_QUIT
#define WM_QUIT 18
#endif
#define DEBUG_CLIPRDR(...) ((void)0)
'''

STUBS = r'''
static int stage, events, loads, unloads, closes, waits, posts, allocations, handles_created;
static int opened, duplicate_closes, worker_started, worker_joined;
static int window_destroys, ole_inits, ole_uninits, pending_frees;
static uintptr_t closed[16];
static void *pending;
static volatile LONG quit_worker, release_worker, window_created, message_entered, worker_returned;
#ifdef _WIN32
static HANDLE real_thread;
#else
static pthread_t real_thread;
static LONG InterlockedExchange(volatile LONG *value,LONG replacement) {
 return __atomic_exchange_n(value,replacement,__ATOMIC_SEQ_CST);
}
static LONG InterlockedCompareExchange(volatile LONG *value,LONG replacement,LONG expected) {
 __atomic_compare_exchange_n(value,&expected,replacement,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
 return expected;
}
#endif
static LONG atomic_read(volatile LONG *value) {return InterlockedCompareExchange(value,0,0);}
static void test_pause(void) {
#ifdef _WIN32
 Sleep(1);
#else
 usleep(1000);
#endif
}
static void reset_counts(int failure) {
 stage=failure; events=loads=unloads=closes=waits=posts=allocations=opened=duplicate_closes=handles_created=0;
 worker_started=worker_joined=window_destroys=ole_inits=ole_uninits=pending_frees=0;
 pending=NULL;memset(closed,0,sizeof(closed));
 InterlockedExchange(&quit_worker,0);InterlockedExchange(&release_worker,0);
 InterlockedExchange(&window_created,0);InterlockedExchange(&message_entered,0);
 InterlockedExchange(&worker_returned,0);
}
static void *fake_calloc(size_t n,size_t size) {
 allocations++; if(stage==1) return NULL; return calloc(n,size);
}
static void fake_free(void *value) {if(value && value==pending) pending_frees++;free(value);}
static HMODULE fake_LoadLibraryA(const char *name) {loads++;return (HMODULE)(uintptr_t)10;}
static BOOL fake_FreeLibrary(HMODULE h) {
 assert(h==(HMODULE)(uintptr_t)10 && (!worker_started || worker_joined));
 assert(ole_inits==ole_uninits);unloads++;return TRUE;
}
static void *fake_GetProcAddress(HMODULE h,const char *name) {return (void*)(uintptr_t)20;}
static HANDLE fake_CreateEvent(void*a,int b,int c,void*d) {
 events++; if((events==1 && stage==2)||(events==2 && stage==4)) return NULL;
 handles_created++;
 return (HANDLE)(uintptr_t)(100+events);
}
static HANDLE fake_CreateMutex(void*a,int b,const char*c) {
 assert(!a && b==FALSE);
 if(stage==3 || (stage==6 && c)) return NULL;
 if(c) assert(stage==7 && strcmp(c,"data_obj_mutex")==0);
 handles_created++;
 return (HANDLE)(uintptr_t)103;
}
static int fake_create_cliprdr_window(wfClipboard *clipboard) {
 if(stage==7) while(!atomic_read(&release_worker)) test_pause();
 if(stage==8) return -1;
 clipboard->hwnd=(HWND)(uintptr_t)200;
 InterlockedExchange(&window_created,1);return 0;
}
static void fake_OleInitialize(int value) {assert(value==0);ole_inits++;}
static void fake_OleUninitialize(void) {assert(ole_inits==1 && ole_uninits==0);ole_uninits++;}
static BOOL fake_DestroyWindow(HWND window) {
 assert(window==(HWND)(uintptr_t)200 && atomic_read(&window_created));
 assert(ole_inits==1 && ole_uninits==0 && window_destroys==0);window_destroys++;return TRUE;
}
static BOOL fake_GetMessage(MSG *msg,HWND window,UINT first,UINT last) {
 assert(!window && !first && !last && atomic_read(&window_created));
 InterlockedExchange(&message_entered,1);
 while(!atomic_read(&quit_worker)) test_pause();
 return 0;
}
static BOOL fake_TranslateMessage(MSG *msg) {return TRUE;}
static BOOL fake_DispatchMessage(MSG *msg) {return TRUE;}
static DWORD(WINAPI *native_worker)(LPVOID);
static LPVOID native_worker_argument;
static DWORD WINAPI actual_worker_entry(LPVOID ignored) {
 DWORD result=native_worker(native_worker_argument);
 InterlockedExchange(&worker_returned,1);return result;
}
#ifndef _WIN32
static void *pthread_worker_entry(void *ignored) {actual_worker_entry(NULL);return NULL;}
#endif
static HANDLE fake_CreateThread(void*a,size_t b,DWORD(WINAPI*c)(LPVOID),LPVOID d,DWORD e,DWORD*f) {
 if(stage==5) return NULL;
 worker_started=1;native_worker=c;native_worker_argument=d;
#ifdef _WIN32
 real_thread=CreateThread(NULL,0,actual_worker_entry,NULL,0,NULL); assert(real_thread);
#else
 assert(pthread_create(&real_thread,NULL,pthread_worker_entry,NULL)==0);
#endif
 if(stage!=7) while(!atomic_read(&window_created) && !atomic_read(&worker_returned)) test_pause();
 if(stage==9) while(!atomic_read(&message_entered)) test_pause();
 handles_created++;
 return (HANDLE)(uintptr_t)104;
}
static BOOL fake_try_open_clipboard(HWND h) {opened++;return FALSE;}
static BOOL fake_is_set_by_instance(wfClipboard*c) {return FALSE;}
static BOOL fake_is_file_descriptor_from_remote(void) {return FALSE;}
static BOOL fake_EmptyClipboard(void) {return TRUE;}
static BOOL fake_CloseClipboard(void) {return TRUE;}
static DWORD fake_GetLastError(void) {return 5;}
static BOOL fake_PostMessage(HWND h,UINT a,uintptr_t b,intptr_t c) {
 assert(h==(HWND)(uintptr_t)200 && a==WM_QUIT && worker_started && !worker_joined); posts++;
 InterlockedExchange(&quit_worker,1);return TRUE;
}
static DWORD fake_WaitForSingleObject(HANDLE h,DWORD timeout) {
 assert(h==(HANDLE)(uintptr_t)104 && timeout==INFINITE && posts<=1 && !worker_joined);waits++;
 if(stage==7) {
  assert(posts==0 && !atomic_read(&window_created));
  puts("SCHEDULE: uninit joins before delayed window exists; no WM_QUIT was posted");fflush(stdout);
  InterlockedExchange(&release_worker,1);
 }
#ifdef _WIN32
 assert(WaitForSingleObject(real_thread,INFINITE)==WAIT_OBJECT_0);assert(CloseHandle(real_thread));
#else
 assert(pthread_join(real_thread,NULL)==0);
#endif
 worker_joined=1;return 0;
}
static BOOL fake_CloseHandle(HANDLE h) {
 assert(h);
 for(int i=0;i<closes;i++) if(closed[i]==(uintptr_t)h) duplicate_closes++;
 if(h==(HANDLE)(uintptr_t)104) assert(worker_joined);
 assert(closes<16);closed[closes++]=(uintptr_t)h;return TRUE;
}
static void fake_wf_destroy_file_obj(void*d) {assert(!"No data object created by test initialization");}
#undef CreateEvent
#undef CreateMutex
#undef GetMessage
#undef DispatchMessage
#undef PostMessage
#define calloc fake_calloc
#define free fake_free
#define LoadLibraryA fake_LoadLibraryA
#define FreeLibrary fake_FreeLibrary
#define GetProcAddress fake_GetProcAddress
#define CreateEvent fake_CreateEvent
#define CreateMutex fake_CreateMutex
#define CreateThread fake_CreateThread
#define create_cliprdr_window fake_create_cliprdr_window
#define OleInitialize fake_OleInitialize
#define OleUninitialize fake_OleUninitialize
#define DestroyWindow fake_DestroyWindow
#define GetMessage fake_GetMessage
#define TranslateMessage fake_TranslateMessage
#define DispatchMessage fake_DispatchMessage
#define try_open_clipboard fake_try_open_clipboard
#define is_set_by_instance fake_is_set_by_instance
#define is_file_descriptor_from_remote fake_is_file_descriptor_from_remote
#define EmptyClipboard fake_EmptyClipboard
#define CloseClipboard fake_CloseClipboard
#define GetLastError fake_GetLastError
#define PostMessage fake_PostMessage
#define WaitForSingleObject fake_WaitForSingleObject
#define CloseHandle fake_CloseHandle
#define wf_destroy_file_obj fake_wf_destroy_file_obj
#define wf_cliprdr_monitor_ready NULL
#define wf_cliprdr_server_capabilities NULL
#define wf_cliprdr_server_format_list NULL
#define wf_cliprdr_server_format_list_response NULL
#define wf_cliprdr_server_lock_clipboard_data NULL
#define wf_cliprdr_server_unlock_clipboard_data NULL
#define wf_cliprdr_server_format_data_request NULL
#define wf_cliprdr_server_format_data_response NULL
#define wf_cliprdr_server_file_contents_request NULL
#define wf_cliprdr_server_file_contents_response NULL
BOOL wf_cliprdr_uninit(wfClipboard*,CliprdrClientContext*);
'''

MAIN = r'''
static wfClipboard clipboard;
BOOL init_cliprdr(CliprdrClientContext *context) {return wf_cliprdr_init(&clipboard,context);}
BOOL uninit_cliprdr(CliprdrClientContext *context) {return wf_cliprdr_uninit(&clipboard,context);}
BOOL empty_cliprdr(CliprdrClientContext *context,UINT32 connID) {return TRUE;}
void control_set_stage(int value) {reset_counts(value);}
int control_is_clean(void) {
 const wfClipboard zero={0};
 return memcmp(&clipboard,&zero,sizeof(clipboard))==0 && loads==unloads && duplicate_closes==0
   && closes==handles_created
   && (!worker_started || (worker_joined && waits==1 && atomic_read(&worker_returned)
        && ole_inits==ole_uninits && (stage==8 ? window_destroys==0 : window_destroys==1)))
   && (!pending || pending_frees==1);
}
#ifndef MIXEL_CLIP_RUST_CALLER
int main(int argc,char **argv) {
 CliprdrClientContext context={0},foreign={0};
 if(argc>1 && strcmp(argv[1],"original-delayed-window")==0) {
  reset_counts(7);assert(init_cliprdr(&context)==TRUE);assert(!clipboard.hwnd);
  uninit_cliprdr(&context);return 0;
 }
 if(argc>1 && (strcmp(argv[1],"original")==0 || strcmp(argv[1],"original-named-collision")==0)) {
  reset_counts(strcmp(argv[1],"original")==0 ? 3 : 6); assert(init_cliprdr(&context)==FALSE);
  assert(clipboard.format_mappings!=NULL);
  puts("ORIGINAL: C init already released map; invoke the Rust error-Box Drop cleanup");fflush(stdout);
  uninit_cliprdr(&context); return 0;
 }
 assert(wf_cliprdr_init(NULL,&context)==FALSE && wf_cliprdr_init(&clipboard,NULL)==FALSE);
 assert(wf_cliprdr_uninit(NULL,&context)==FALSE && wf_cliprdr_uninit(&clipboard,NULL)==FALSE);
 for(int cycle=0;cycle<3;cycle++) {
  for(int failure=1;failure<=5;failure++) {
   reset_counts(failure); assert(init_cliprdr(&context)==FALSE); assert(control_is_clean());
   int before_closes=closes,before_opened=opened;
   assert(uninit_cliprdr(&context)==TRUE); assert(control_is_clean());
   assert(closes==before_closes && opened==before_opened);
   reset_counts(0);assert(init_cliprdr(&context)==TRUE && clipboard.context==&context);
   assert(context.Custom==&clipboard && worker_started && !worker_joined);
   assert(init_cliprdr(&foreign)==FALSE);
   assert(uninit_cliprdr(&foreign)==TRUE && clipboard.context==&context && !worker_joined);
   clipboard.format_mappings[0].name=malloc(8);
   pending=malloc(19);assert(pending);clipboard.req_fdata=pending;
   assert(uninit_cliprdr(&context)==TRUE && context.Custom==NULL && control_is_clean());
   assert(closes==4 && posts==1 && waits==1);
   assert(uninit_cliprdr(&context)==TRUE && control_is_clean() && closes==4);
  }
 }
 for(int schedule=7;schedule<=9;schedule++) {
  reset_counts(schedule);assert(init_cliprdr(&context)==TRUE);
  pending=malloc(23);assert(pending);clipboard.req_fdata=pending;
  assert(uninit_cliprdr(&context)==TRUE && control_is_clean());
  assert(uninit_cliprdr(&context)==TRUE && pending_frees==1 && closes==4);
  if(schedule==7) assert(posts==0 && !atomic_read(&message_entered) && window_destroys==1);
  if(schedule==8) assert(posts==0 && window_destroys==0);
  if(schedule==9) assert(posts==1 && atomic_read(&message_entered) && window_destroys==1);
 }
 reset_counts(6);assert(init_cliprdr(&foreign)==TRUE);
 assert(uninit_cliprdr(&context)==TRUE && clipboard.context==&foreign && !worker_joined);
 assert(uninit_cliprdr(&foreign)==TRUE && control_is_clean());
 puts("PASS: exact C five init-failure boundaries x3, named-mutex collision bypassed only by unnamed context mutex, error-Drop/duplicate/foreign cleanup, failed concurrent init, successful reinit, actual extracted worker late-window/already-blocked/failed-window shutdown, window destroy before OleUninitialize, pending-response freed exactly once, balanced module and resource ownership");
 return 0;
}
#endif
'''


def c_source(native_source):
    start = native_source.index("typedef BOOL(WINAPI *fnAddClipboardFormatListener)")
    end = native_source.index("struct _CliprdrEnumFORMATETC", start)
    typedefs = native_source[start:end]
    start = native_source.index("struct wf_clipboard\n")
    end = native_source.index("typedef struct wf_clipboard wfClipboard;", start) + len("typedef struct wf_clipboard wfClipboard;")
    structure = native_source[start:end]
    functions = [function(native_source, name) for name in ("cliprdr_thread_func", "clear_file_array", "clear_format_map", "wf_cliprdr_init", "wf_cliprdr_uninit")]
    alignment = ("\n_Static_assert(offsetof(wfClipboard, stopping) % sizeof(LONG) == 0, \"Interlocked stop flag alignment\");\n"
                 if patcher.MARKER in native_source else "")
    return PRELUDE + typedefs + structure + alignment + STUBS + "\n\n".join(functions) + MAIN


def checked(command, output, name, expected=0, contains=None, environment=None):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=60, env=environment)
    (output / (name + ".log")).write_text(result.stdout)
    if expected == 0:
        assert result.returncode == 0, result.stdout
    else:
        assert result.returncode != 0, result.stdout
    if contains:
        assert contains in result.stdout, result.stdout
    print(f"PASS: {name}; actual exit {result.returncode}")
    return result


def unix_compiler():
    # Use GCC's own instrumentation and runtime together on Linux; Apple Clang
    # supplies the actual SDK/compiler runtime on macOS.
    compiler = os.environ.get("CC") or shutil.which("clang" if sys.platform == "darwin" else "gcc")
    assert compiler, "Native C compiler required"
    return compiler


def compile_c(path, executable, output, label, obj=False):
    if os.name == "nt":
        compiler = shutil.which("cl")
        assert compiler, "MSVC developer environment required"
        command = [compiler, "/nologo", "/std:c11", "/MD", "/Zi", "/fsanitize=address"]
        if obj:
            command += ["/c", "/DMIXEL_CLIP_RUST_CALLER", str(path), "/Fo" + str(executable)]
        else:
            command += [str(path), "/Fe" + str(executable)]
    else:
        compiler = unix_compiler()
        command = [compiler, "-std=c11", "-D_DEFAULT_SOURCE", "-g", "-O0", "-fsanitize=address", "-pthread"]
        if obj:
            command += ["-DMIXEL_CLIP_RUST_CALLER", "-c"]
        command += [str(path), "-o", str(executable)]
    checked(command, output, label)


def rust_source(platform_source):
    start = platform_source.index("pub type size_t =")
    context_end = platform_source.index("impl CliprdrServiceContext for", start)
    assert hashlib.sha256(platform_source[start:context_end].encode()).hexdigest() == (
        "9afd89ce9c7ff36dea919f87de556867ea6f135b2deb440427996ffdb167bd69"
    ), "Pinned Rust clipboard types/create/Drop changed"
    end = platform_source.index("impl CliprdrClientContext {", start)
    types = platform_source[start:end]
    start = end
    end = platform_source.index("impl CliprdrServiceContext for", start)
    implementations = platform_source[start:end]
    return "#![allow(dead_code, non_camel_case_types, non_snake_case)]\n#[derive(Debug)] pub enum CliprdrError { CliprdrInit }\n" + types + implementations + r'''
extern "C" {fn control_set_stage(value:i32);fn control_is_clean()->i32;}
fn main() {
 let original=std::env::args().any(|a|a=="original");
 let stages=if original {vec![3]} else {vec![1,2,3,4,5,0,1,3,0,6,7,8,9]};
 for stage in stages {
  unsafe {control_set_stage(stage)};
  let result=CliprdrClientContext::create(true,false,30,None,None,None,None,None,None,None,None);
  assert_eq!(result.is_err(),stage>=1 && stage<=5);
  if stage==0 {
   let foreign=CliprdrClientContext::create(true,false,30,None,None,None,None,None,None,None,None);
   assert!(foreign.is_err());
   assert!(!result.as_ref().unwrap().Custom.is_null());
   drop(foreign);
  }
  drop(result);
  if !original {assert_eq!(unsafe {control_is_clean()},1);}
 }
 println!("PASS: actual pinned Rust Box create/error/Drop caller and actual extracted C ownership control");
}
'''


def run(output):
    upstream = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
    source = (upstream / patcher.SOURCE).read_text()
    if patcher.MARKER in source:
        patcher.patch(source)
        for old, new in patcher.REPLACEMENTS:
            source = source.replace(new, old, 1)
    assert hashlib.sha256(source.encode()).hexdigest() == patcher.SOURCE_SHA256
    corrected = patcher.patch(source)
    assert corrected != source and patcher.patch(corrected) == corrected
    for drift in (source + "\n", source.replace("map_capacity = 32", "map_capacity = 64"),
                  corrected + "\n", corrected.replace("memset(clipboard, 0", "memset(clipboard, 1")):
        try:
            patcher.patch(drift)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Source drift accepted")
    print("PASS: exact pin, all changes validated before write, idempotence and four original/patched drift controls")
    (output / "cliprdr.h").write_bytes((upstream / "libs/clipboard/src/cliprdr.h").read_bytes())
    files = {}
    for label, text in (("original", source), ("corrected", corrected)):
        path = output / (label + ".c")
        path.write_text(c_source(text))
        executable = output / (label + (".exe" if os.name == "nt" else "-native"))
        compile_c(path, executable, output, label + "-compile")
        checked([str(executable), label], output, label + "-native",
                expected=1 if label == "original" else 0,
                contains="AddressSanitizer: heap-use-after-free" if label == "original" else "PASS: exact C")
        if label == "original":
            checked([str(executable), "original-named-collision"], output, "original-named-collision-native",
                    expected=1, contains="AddressSanitizer: heap-use-after-free")
            try:
                result = subprocess.run([str(executable), "original-delayed-window"],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, timeout=10)
            except subprocess.TimeoutExpired as failure:
                log = failure.stdout or ""
                if isinstance(log, bytes):
                    log = log.decode("utf-8", errors="replace")
                assert "uninit joins before delayed window exists" in log, log
                (output / "original-delayed-window-native.log").write_text(
                    log + "EXPECTED: original actual worker remains blocked; bounded subprocess timeout killed its owned test process\n")
                print("PASS: original actual worker delayed-window shutdown hangs; bounded 10-second negative control")
            else:
                raise AssertionError("Original worker unexpectedly completed delayed-window shutdown: " + result.stdout)
        files[label] = path
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    assert rustc, "Native Rust compiler required to exercise the actual error-Box Drop"
    rust = output / "actual-rust-context-create-drop.rs"
    rust.write_text(rust_source((upstream / "libs/clipboard/src/platform/windows.rs").read_text()))
    for label, path in files.items():
        obj = output / (label + (".obj" if os.name == "nt" else ".o"))
        compile_c(path, obj, output, label + "-object-compile", obj=True)
        executable = output / (label + "-rust-caller" + (".exe" if os.name == "nt" else ""))
        command = rustc_command([rustc, "--edition=2021", str(rust), "-o", str(executable), "-C", "link-arg=" + str(obj)])
        native_environment = None
        if os.name == "nt":
            # MSVC's ASan C object carries the runtime directives into the linker.
            command += ["-C", "link-arg=/INCREMENTAL:NO"]
        elif sys.platform == "darwin":
            compiler = unix_compiler()
            runtime = Path(subprocess.run([compiler, "-print-file-name=libclang_rt.asan_osx_dynamic.dylib"],
                                         check=True, capture_output=True, text=True).stdout.strip())
            assert runtime.is_absolute() and runtime.is_file(), "Actual compiler ASan runtime absent"
            command += ["-C", "link-arg=" + str(runtime), "-C", "link-arg=-Wl,-rpath," + str(runtime.parent)]
        else:
            compiler = unix_compiler()
            runtime = Path(subprocess.run([compiler, "-print-file-name=libasan.so"],
                                         check=True, capture_output=True, text=True).stdout.strip())
            assert runtime.is_absolute() and runtime.is_file(), "Actual compiler ASan runtime absent"
            support = Path(subprocess.run([compiler, "-print-libgcc-file-name"],
                                         check=True, capture_output=True, text=True).stdout.strip())
            assert support.is_absolute() and support.is_file(), "Actual compiler support archive absent"
            # GCC AArch64 may emit outlined atomic helpers. rustc's default
            # support libraries precede the explicit C object, so put this
            # compiler's archive after it to resolve only its required helpers.
            command += ["-C", "link-arg=" + str(runtime), "-C", "link-arg=" + str(support),
                        "-C", "link-arg=-pthread"]
            # rustc's standard system libraries precede user link arguments.
            # Place only this verified compiler runtime first in this test
            # process; otherwise ASan rejects initialization before exercising
            # the actual C/Rust ownership contract. The product build and parent
            # environment are untouched.
            assert not any(c.isspace() or c == ':' for c in str(runtime)), "ASan runtime path is not a safe preload member"
            native_environment = os.environ.copy()
            previous = native_environment.get("LD_PRELOAD", "")
            native_environment["LD_PRELOAD"] = str(runtime) + (" " + previous if previous else "")
        checked(command, output, label + "-rust-compile")
        checked([str(executable), label], output, label + "-actual-rust-drop",
                expected=1 if label == "original" else 0,
                contains="AddressSanitizer: heap-use-after-free" if label == "original" else "PASS: actual pinned Rust",
                environment=native_environment)
    print("PASS: original C and actual Rust error-Drop both fail under AddressSanitizer; corrected actual C/Rust resource ownership passes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if args.evidence_dir:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        run(args.evidence_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="mixel-windows-clipboard-") as directory:
            run(Path(directory))
