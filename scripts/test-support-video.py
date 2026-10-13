#!/usr/bin/env python3
"""Execute the actual original/fixed Rust tile caller against C varargs.

The C receiver uses the installed libaom API header and its control typedefs.
It records arguments; no codec selection, video algorithm or application runs.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
spec = importlib.util.spec_from_file_location("mixel_video_patch", ROOT / "scripts/patch-support-video.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def run(argv, **options):
    return subprocess.run(argv, check=True, text=True, encoding="utf-8", **options)


def source(relative):
    return run(["git", "-C", str(UPSTREAM), "show", "HEAD:" + relative], capture_output=True).stdout


def function(text, name):
    start = text.index(name)
    opening = text.index("{", start)
    depth = 1
    for index in range(opening + 1, len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            return text[start:index + 1]
    raise RuntimeError("Incomplete pinned AOM caller")


def reject(operation, message):
    try:
        operation()
    except RuntimeError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError("Unexpected video patch drift accepted")


def aom_include():
    candidates = []
    if os.environ.get("AOM_INCLUDE_DIR"):
        candidates.append(Path(os.environ["AOM_INCLUDE_DIR"]))
    triplet = "x64-windows-static" if os.name == "nt" else (
        "arm64-osx" if platform.system() == "Darwin" and platform.machine() == "arm64"
        else "x64-osx" if platform.system() == "Darwin" else "x64-linux")
    if os.environ.get("VCPKG_INSTALLED_ROOT"):
        candidates.append(Path(os.environ["VCPKG_INSTALLED_ROOT"]) / triplet / "include")
    if os.environ.get("VCPKG_ROOT"):
        candidates.append(Path(os.environ["VCPKG_ROOT"]) / "installed" / triplet / "include")
    candidates.extend([Path("/opt/homebrew/include"), Path("/usr/local/include"), Path("/usr/include")])
    for candidate in candidates:
        if (candidate / "aom/aomcx.h").is_file() and (candidate / "aom/aom_codec.h").is_file():
            return candidate.resolve()
    raise RuntimeError("Actual libaom API headers required; set AOM_INCLUDE_DIR or install pinned vcpkg dependencies")


def tile_caller(text):
    body = function(text, "    pub fn set_controls(")
    macro = function(body, "        macro_rules! call_ctl")
    start = body.index("        let tile_set =")
    end = body.index("        call_ctl!(ctx, AV1E_SET_ROW_MT", start)
    return macro, body[start:end]


original = source(patcher.SOURCE)
assert hashlib.sha256(original.encode()).hexdigest() == patcher.ORIGINAL_SHA256
fixed = patcher.patch_source(original)
assert fixed.replace(patcher.NEW, patcher.OLD, 1) == original
include = aom_include()
header = (include / "aom/aomcx.h").read_text(encoding="utf-8")
types = dict(re.findall(r"AOM_CTRL_USE_TYPE\(\s*([A-Z0-9_]+)\s*,\s*([^\)]+)\)", header))
assert types["AV1E_SET_TILE_ROWS"] == types["AV1E_SET_TILE_COLUMNS"] == "unsigned int"
controls = function(original, "    pub fn set_controls(")
names = set(re.findall(r"call_ctl!\(\s*ctx\s*,\s*([A-Z0-9_]+)", controls))
for name in names:
    assert types[name].strip() in ("int", "unsigned int"), (name, types[name])
assert controls.count("as f64") == 1 and controls.count("log2().ceil()") == 1
print("Source provenance: pinned AOM SHA256=" + patcher.ORIGINAL_SHA256)
print("Header provenance: " + str(include) + " aomcx.h SHA256="
      + hashlib.sha256((include / "aom/aomcx.h").read_bytes()).hexdigest()
      + " aom_codec.h SHA256=" + hashlib.sha256((include / "aom/aom_codec.h").read_bytes()).hexdigest())
print("Primary header resolved path: " + str((include / "aom/aomcx.h").resolve()))
print(f"PASS: nearby {len(names)} named AOM variadic controls use representable 32-bit integer/enum values; the dynamic tile row/column argument was the sole floating value")

C = r'''#include <aom/aomcx.h>
#include <stdarg.h>
_Static_assert(sizeof(unsigned int) == 4, "Rust u32 requires a 32-bit C unsigned int");
_Static_assert(_Generic((aom_codec_control_type_AV1E_SET_TILE_COLUMNS)0,
                       unsigned int: 1, default: 0), "Tile columns require unsigned int");
_Static_assert(_Generic((aom_codec_control_type_AV1E_SET_TILE_ROWS)0,
                       unsigned int: 1, default: 0), "Tile rows require unsigned int");
static unsigned int observed;
static int control;
aom_codec_err_t aom_codec_control(aom_codec_ctx_t *ctx, int ctrl_id, ...) {
  (void)ctx;
  va_list arguments;
  va_start(arguments, ctrl_id);
  observed = va_arg(arguments, unsigned int);
  va_end(arguments);
  control = ctrl_id;
  return AOM_CODEC_OK;
}
unsigned int mixel_observed_value(void) { return observed; }
int mixel_observed_control(void) { return control; }
int mixel_tile_columns(void) { return AV1E_SET_TILE_COLUMNS; }
int mixel_tile_rows(void) { return AV1E_SET_TILE_ROWS; }
'''
RUST = r'''#![allow(dead_code, non_snake_case)]
use std::ffi::c_void;
extern "C" {
 fn aom_codec_control(ctx:*mut c_void,control:i32,...)->i32;
 fn mixel_observed_value()->u32;
 fn mixel_observed_control()->i32;
 fn mixel_tile_columns()->i32;
 fn mixel_tile_rows()->i32;
}
macro_rules! call_aom_allow_err {
 ($call:expr)=>{{assert_eq!(unsafe{$call},0,"C API call failed");}};
}
struct Config {g_threads:u32,g_w:u32,g_h:u32}
fn original(cfg:&Config)->(i32,u32) {
 let ctx=std::ptr::null_mut();
 let AV1E_SET_TILE_COLUMNS=unsafe{mixel_tile_columns()};
 let AV1E_SET_TILE_ROWS=unsafe{mixel_tile_rows()};
 __ORIGINAL_MACRO__
 __ORIGINAL_CALLER__
 unsafe{(mixel_observed_control(),mixel_observed_value())}
}
fn fixed(cfg:&Config)->(i32,u32) {
 let ctx=std::ptr::null_mut();
 let AV1E_SET_TILE_COLUMNS=unsafe{mixel_tile_columns()};
 let AV1E_SET_TILE_ROWS=unsafe{mixel_tile_rows()};
 __FIXED_MACRO__
 __FIXED_CALLER__
 unsafe{(mixel_observed_control(),mixel_observed_value())}
}
fn main() {
 let mut mismatches=0;
 for rows in [false,true] {
  for threads in [1,2,3,4,8,16,128] {
   let cfg=Config{g_threads:threads,g_w:if rows{640}else{1920},g_h:if rows{480}else{1080}};
   let expected=if threads<=1{0}else{u32::BITS-(threads-1).leading_zeros()};
   let control=unsafe{if rows && threads==4{mixel_tile_rows()}else{mixel_tile_columns()}};
   let before=original(&cfg);let after=fixed(&cfg);
   if before!=(control,expected){mismatches+=1;}
   assert_eq!(after,(control,expected),"actual generated unsigned tile caller differs");
   println!("PASS actual C-varargs: {} threads={} original={} fixed={} expected={}",
            if rows && threads==4{"rows"}else{"columns"},threads,before.1,after.1,expected);
  }
 }
 assert!(mismatches>0,"Original double argument unexpectedly passed every actual unsigned C-varargs read");
 println!("PASS original negative control: {} actual C-varargs argument mismatches",mismatches);
}
'''

with tempfile.TemporaryDirectory(prefix="mixel-video-control-") as temporary:
    work = Path(temporary)
    repo = work / "generated"
    (repo / patcher.SOURCE).parent.mkdir(parents=True)
    (repo / "Cargo.toml").write_text(source("Cargo.toml"), encoding="utf-8")
    target = repo / patcher.SOURCE
    target.write_text(original, encoding="utf-8")
    patcher.apply(repo)
    first = target.read_bytes()
    patcher.apply(repo)
    assert target.read_bytes() == first == fixed.encode()
    reject(lambda: patcher.patch_source(original.replace("pub struct AomEncoder", "pub struct DifferentEncoder")), "source changed")
    reject(lambda: patcher.patch_source(fixed.replace("as u32);", "as u64);", 1)), "source changed")
    reject(lambda: patcher.patch_source(original + "\n"), "source changed")
    manifest = repo / "Cargo.toml"
    manifest.write_text(manifest.read_text().replace('version = "1.4.6"', 'version = "1.4.7"', 1))
    reject(lambda: patcher.apply(repo), "version 1.4.6")
    assert target.read_bytes() == first, "Version drift must not mutate source"
    print("PASS: exact pinned source/version required; patch is byte-idempotent and source/version/modified-fix drift fails before writes")
    original_macro, original_caller = tile_caller(original)
    fixed_macro, fixed_caller = tile_caller(target.read_text())
    cfile = work / "control.c"
    cfile.write_text(C)
    obj = work / ("control.obj" if os.name == "nt" else "control.o")
    compiler = os.environ.get("CC") or shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
    assert compiler, "Native C compiler required"
    run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I" + str(include), "-c", str(cfile), "-o", str(obj)])
    rust = work / "caller.rs"
    rust.write_text(RUST.replace("__ORIGINAL_MACRO__", original_macro).replace("__ORIGINAL_CALLER__", original_caller)
                    .replace("__FIXED_MACRO__", fixed_macro).replace("__FIXED_CALLER__", fixed_caller))
    binary = work / ("caller.exe" if os.name == "nt" else "caller")
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    assert rustc, "Native Rust compiler required"
    run(rustc_command([rustc, "--edition=2021", "--deny=warnings", str(rust), "-C", "link-arg=" + str(obj), "-o", str(binary)]))
    run([str(binary)])
print("Result: pinned AV1 unsigned tile-control source and actual C-varargs regression checks passed")
