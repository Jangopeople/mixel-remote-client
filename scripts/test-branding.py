#!/usr/bin/env python3
"""Run the complete branding pipeline on a fresh pinned source clone."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()
SCRIPT = ROOT / "scripts/apply-branding.sh"
BASH = "bash"
if os.name == "nt":
    # Windows can put the WSL/System32 bash.exe before Git Bash on PATH. The
    # branding script requires the Git/MSYS tools used by Actions shell: bash.
    git = shutil.which("git")
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"]
    if git:
        candidates.append(Path(git).resolve().parent.parent / "bin/bash.exe")
        candidates.append(Path(git).resolve().parent.parent / "usr/bin/bash.exe")
    selected = next((candidate for candidate in candidates if candidate.is_file()), None)
    if selected is None:
        raise RuntimeError("Native Windows branding regression requires the installed Git Bash executable")
    BASH = str(selected)
BRANDING_COMMAND = [BASH, SCRIPT.as_posix()]


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command[0]}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result


def snapshot(repo: Path) -> dict[str, dict[str, object]]:
    result = {}
    for source in (repo, repo / "libs/hbb_common", repo / ".mixel-deps/rdev", repo / ".mixel-deps/clipboard-master"):
        paths = run(["git", "-C", str(source), "ls-files", "--cached", "--others", "--exclude-standard"]).stdout.splitlines()
        for path in paths:
            target = source / path
            if target.is_file():
                data = target.read_bytes()
                crlf = data.count(b"\r\n")
                result[str(target.relative_to(repo))] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "crlf": crlf,
                    "bare_lf": data.count(b"\n") - crlf,
                    "sha256_without_crlf": hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
                }
    return result


def config_path_function(source: str) -> str:
    start = source.index("    pub fn path<P: AsRef<Path>>(p: P) -> PathBuf {")
    end = source.index("\n    pub fn", start + 1)
    return source[start:end]


with tempfile.TemporaryDirectory(prefix="mixel-branding-") as directory:
    base = Path(directory)
    repo = base / "rustdesk"
    run(["git", "clone", "--quiet", "--shared", "--branch", "1.4.6", str(SOURCE), str(repo)])
    run(["git", "clone", "--quiet", "--shared", str(SOURCE / "libs/hbb_common"), str(repo / "libs/hbb_common")])
    submodule_commit = run(["git", "-C", str(repo), "ls-tree", "HEAD", "libs/hbb_common"]).stdout.split()[2]
    run(["git", "-C", str(repo / "libs/hbb_common"), "checkout", "--quiet", submodule_commit])
    original_config = (repo / "libs/hbb_common/src/config.rs").read_text(encoding="utf-8")
    # Deliberately omit RDREPO. The documented default is the current working
    # directory's ./rustdesk; the Python patch must never hit the original clone.
    env = {key: value for key, value in os.environ.items() if key != "RDREPO"}
    env["BRANDING"] = (ROOT / "branding").as_posix()
    first = run(BRANDING_COMMAND, cwd=base, env=env)
    assert "native loaders, protocol handlers, Linux app identity and relay defaults verified" in first.stdout
    assert (repo / "flutter/lib/mixel_support_invite.dart").is_file()
    print("PASS: complete branding and support patch use the same fresh 1.4.6 checkout with default RDREPO")

    gtk = (repo / "flutter/linux/my_application.cc").read_text(encoding="utf-8")
    assert 'gtk_icon_theme_load_icon(theme, "mixel-remote",' in gtk
    assert 'gtk_header_bar_set_title(header_bar, "Mixel Remote");' in gtk
    assert 'gtk_window_set_title(window, "Mixel Remote");' in gtk
    bus = (repo / "src/server/dbus.rs").read_text(encoding="utf-8")
    assert 'const DBUS_NAME: &str = "ch.mixel.remote";' in bus
    assert 'proxy.method_call(DBUS_NAME, DBUS_METHOD_NEW_CONNECTION, (uni_links,))?' in bus
    assert 'conn.request_name(DBUS_NAME, false, true, false)?' in bus
    build = (repo / "build.py").read_text(encoding="utf-8")
    assert 'strip {flutter_build_dir}/lib/libmixel-remote.so' in build
    assert 'apps/mixel-remote.svg' not in build
    assert '[patch."https://github.com/rustdesk-org/rdev"]' in (repo / "Cargo.toml").read_text(encoding="utf-8")
    assert 'Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,' in (repo / ".mixel-deps/rdev/src/linux/grab.rs").read_text(encoding="utf-8")
    assert '[patch."https://github.com/rustdesk-org/clipboard-master"]' in (repo / "Cargo.toml").read_text(encoding="utf-8")
    clipboard = (repo / ".mixel-deps/clipboard-master/src/master/x11.rs").read_text(encoding="utf-8")
    assert "if subscribed_sequence.is_none()" in clipboard
    assert "Event::XfixesSelectionNotify" in clipboard and "event.selection == selection" in clipboard
    plist = plistlib.loads((repo / "flutter/macos/Runner/Info.plist").read_bytes())
    assert plist["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["mixel-remote"]
    common = (repo / "src/common.rs").read_text(encoding="utf-8")
    assert '"ch.mixel.remote".to_owned()' in common.split("pub fn get_full_name() -> String {", 1)[1].split("}\n", 1)[0]
    config = (repo / "libs/hbb_common/src/config.rs").read_text(encoding="utf-8")
    assert 'RwLock::new("com.carriez".to_owned())' in config, "retain the existing preference namespace"
    assert config_path_function(config) == config_path_function(original_config), "keep actual config path resolution byte-identical"
    macos = (repo / "src/platform/macos.rs").read_text(encoding="utf-8")
    helper_calls = [line.strip() for line in macos.splitlines() if "get_full_name()" in line]
    assert helper_calls == [
        'let daemon = format!("{}_service.plist", crate::get_full_name());',
        'let agent = format!("{}_server.plist", crate::get_full_name());',
        'let agent = format!("{}_server.plist", crate::get_full_name());',
        '.args(&["remove", &format!("{}_server", crate::get_full_name())])',
        'let agent = format!("{}_server.plist", crate::get_full_name());',
    ], "helper naming must affect only daemon/agent detection, update and removal"
    run([sys.executable, str(ROOT / "scripts/test-macos-service-label.py")])
    print("PASS: all five actual macOS helper uses agree on canonical service labels; ORG and config path resolution retained")
    print("PASS: branded GTK icon/titles, isolated Linux DBus, bundled library strip and macOS protocol registration")

    # Execute the pinned converter before the actual generated normalization
    # and dispatch. WideCharToMultiByte(-1) includes a NUL in std::string::size;
    # plain string test inputs cannot reproduce that customer command line.
    windows_main = (repo / "flutter/windows/runner/main.cpp").read_text(encoding="utf-8")
    normalize = windows_main[windows_main.index("  // Remove possible trailing whitespace"):windows_main.index("\n\n  int args_len")]
    def window_dispatch(source: str) -> str:
        return source[source.index("  // Uri links dispatch"):source.index("  // Attach to console")]

    dispatch = window_dispatch(windows_main)
    upstream_windows_main = run(["git", "-C", str(repo), "show", "HEAD:flutter/windows/runner/main.cpp"]).stdout
    upstream_dispatch = window_dispatch(upstream_windows_main)
    upstream_normalize = upstream_windows_main[upstream_windows_main.index("  // Remove possible trailing whitespace"):upstream_windows_main.index("\n\n  int args_len")]
    sentinel_guard = '''    // Utf8FromUtf16 includes the command-line UTF-8 NUL sentinel.
    // Remove it before trimming whitespace, preserving every argument byte.
    if (!argument.empty() && argument.back() == '\\0') argument.pop_back();
'''
    assert normalize.count(sentinel_guard) == 1
    previous_normalize = normalize.replace(sentinel_guard, "", 1)
    assert 'argument.erase(last == std::string::npos ? 0 : last + 1);' in previous_normalize
    utils = (repo / "flutter/windows/runner/utils.cpp").read_text(encoding="utf-8")
    upstream_utils = run(["git", "-C", str(repo), "show", "HEAD:flutter/windows/runner/utils.cpp"]).stdout
    assert utils == upstream_utils, "The pinned Windows UTF-16 converter must remain unchanged"
    converter = utils[utils.index("std::string Utf8FromUtf16("):]
    whitelist = windows_main[windows_main.index("const std::vector<std::string> parameters_white_list ="):]
    whitelist = whitelist[:whitelist.index(";") + 1]
    cpp = base / "windows-arguments"
    cpp.mkdir()
    (cpp / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.16)
project(mixel_window_arguments LANGUAGES CXX)
add_executable(window_arguments main.cpp)
add_executable(window_arguments_original original.cpp)
add_executable(window_arguments_previous_normalizer previous_normalizer.cpp)
add_executable(window_arguments_upstream_normalizer upstream_normalizer.cpp)
foreach(window_target IN ITEMS window_arguments window_arguments_original window_arguments_previous_normalizer window_arguments_upstream_normalizer)
  target_sources(${window_target} PRIVATE converter.cpp)
  target_compile_features(${window_target} PRIVATE cxx_std_17)
  if(WIN32)
    target_link_libraries(${window_target} PRIVATE kernel32)
  endif()
  if(MSVC)
    target_compile_options(${window_target} PRIVATE /W4 /WX)
  else()
    target_compile_options(${window_target} PRIVATE -Wall -Wextra -Werror)
  endif()
endforeach()
''', encoding="utf-8")
    # Windows compiles the exact converter against the real kernel32 API. The
    # non-Windows backend implements its documented UTF-16 and terminating-NUL
    # contract only; it never substitutes for native customer runtime proof.
    (cpp / "converter.cpp").write_text('''#include <cstdint>
#include <string>
#ifdef _WIN32
#include <windows.h>
const char* converter_backend_name() { return "native Windows WideCharToMultiByte"; }
#else
constexpr unsigned int CP_UTF8 = 65001;
constexpr unsigned long WC_ERR_INVALID_CHARS = 0x80;
const char* converter_backend_name() { return "non-Windows documented UTF-16/NUL contract backend"; }
int WideCharToMultiByte(unsigned int page, unsigned long flags, const wchar_t* source,
    int source_length, char* target, int capacity, const char* fallback, int* used_fallback) {
  if (page != CP_UTF8 || flags != WC_ERR_INVALID_CHARS || !source ||
      source_length != -1 || capacity < 0 || fallback || used_fallback) return 0;
  std::string encoded;
  for (std::size_t index = 0; source[index]; ++index) {
    std::uint32_t value = static_cast<std::uint32_t>(source[index]);
    if (value > 0xffff) return 0; // Inputs are actual UTF-16 code units.
    if (value >= 0xd800 && value <= 0xdbff) {
      const auto low = static_cast<std::uint32_t>(source[++index]);
      if (low < 0xdc00 || low > 0xdfff) return 0;
      value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
    } else if (value >= 0xdc00 && value <= 0xdfff) return 0;
    if (value < 0x80) encoded.push_back(static_cast<char>(value));
    else if (value < 0x800) {
      encoded.push_back(static_cast<char>(0xc0 | (value >> 6)));
      encoded.push_back(static_cast<char>(0x80 | (value & 0x3f)));
    } else if (value < 0x10000) {
      encoded.push_back(static_cast<char>(0xe0 | (value >> 12)));
      encoded.push_back(static_cast<char>(0x80 | ((value >> 6) & 0x3f)));
      encoded.push_back(static_cast<char>(0x80 | (value & 0x3f)));
    } else {
      encoded.push_back(static_cast<char>(0xf0 | (value >> 18)));
      encoded.push_back(static_cast<char>(0x80 | ((value >> 12) & 0x3f)));
      encoded.push_back(static_cast<char>(0x80 | ((value >> 6) & 0x3f)));
      encoded.push_back(static_cast<char>(0x80 | (value & 0x3f)));
    }
  }
  encoded.push_back('\\0');
  if (!target && capacity == 0) return static_cast<int>(encoded.size());
  if (!target || capacity < static_cast<int>(encoded.size())) return 0;
  encoded.copy(target, encoded.size());
  return static_cast<int>(encoded.size());
}
#endif
// Keep the pinned converter verbatim, including its existing int/size_t
// comparison. Suppress only that upstream warning around this exact function;
// the fixture and changed generated code still compile with warnings as errors.
#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable: 4018)
#else
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#endif
''' + converter + '''
#ifdef _MSC_VER
#pragma warning(pop)
#else
#pragma GCC diagnostic pop
#endif
''', encoding="utf-8")
    cpp_prefix = '''#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
std::string Utf8FromUtf16(const wchar_t*);
const char* converter_backend_name();
void normalize(std::vector<std::string>& command_line_arguments) {
''' + normalize + '''
}
std::vector<std::string> native_arguments(std::initializer_list<const wchar_t*> arguments) {
  std::vector<std::string> result;
  for (const auto* argument : arguments) result.push_back(Utf8FromUtf16(argument));
  return result;
}
using HWND = void*;
using DWORD = std::uint32_t;
using DWORD_PTR = std::uintptr_t;
using LPARAM = std::intptr_t;
struct COPYDATASTRUCT { DWORD_PTR dwData; DWORD cbData; void* lpData; };
constexpr int SW_NORMAL = 1;
constexpr int SW_RESTORE = 9;
constexpr unsigned int WM_COPYDATA = 74;
constexpr DWORD_PTR UNI_LINKS_DESKTOP_MSG_ID = 1026;
constexpr unsigned int SMTO_BLOCK = 1;
constexpr unsigned int SMTO_ABORTIFHUNG = 2;
int main_window;
bool existing_window = true;
bool minimized = true;
int show_calls = 0;
int foreground_calls = 0;
int uri_dispatch_calls = 0;
int bounded_handoff_calls = 0;
bool delivery_success = true;
HWND shown_window = nullptr;
HWND foreground_window = nullptr;
std::vector<std::string> plugin_argv;
std::string emitted_uri;
const wchar_t* getWindowClassName() { return L"fixture-class"; }
HWND FindWindowW(const wchar_t*, const wchar_t*) {
  return existing_window ? &main_window : nullptr;
}
int ShowWindow(HWND hwnd, int command) {
  ++show_calls;
  shown_window = hwnd;
  if (command == SW_RESTORE || command == SW_NORMAL) minimized = false;
  return 1;
}
int SetForegroundWindow(HWND hwnd) {
  ++foreground_calls;
  foreground_window = hwnd;
  return 1;
}
void DispatchToUniLinksDesktop(HWND) {
  ++uri_dispatch_calls;
  // Pinned uni_links_desktop 0.1.7 sends only argv[1] as a URI string.
  // It cannot deliver --quick_support as the Dart cmdArgs test assumed.
  emitted_uri = plugin_argv.empty() ? "" : plugin_argv.front();
}
int SendMessageTimeoutW(HWND hwnd, unsigned int message, std::uintptr_t wparam,
    LPARAM lparam, unsigned int flags, unsigned int timeout, DWORD_PTR* result) {
  ++bounded_handoff_calls;
  const auto& data = *reinterpret_cast<const COPYDATASTRUCT*>(lparam);
  const char expected[] = "--quick_support";
  if (hwnd != &main_window || message != WM_COPYDATA || wparam != 0 ||
      flags != (SMTO_BLOCK | SMTO_ABORTIFHUNG) || timeout != 5000 ||
      data.dwData != UNI_LINKS_DESKTOP_MSG_ID || data.cbData != sizeof(expected) ||
      std::memcmp(data.lpData, expected, sizeof(expected)) != 0) {
    throw std::runtime_error("Native handoff differs from pinned plugin framing or is unbounded");
  }
  emitted_uri = static_cast<const char*>(data.lpData);
  *result = 0; // The pinned plugin uses the default LRESULT after delivering.
  return delivery_success ? 1 : 0;
}
''' + whitelist + '''
int existing_window_dispatch(std::vector<std::string> command_line_arguments,
    std::vector<std::string> rust_args = {}) {
  (void)rust_args; // The pinned original dispatch does not use core arguments.
  normalize(command_line_arguments);
  plugin_argv = command_line_arguments;
  const std::wstring app_name = L"Mixel-Remote";
'''
    cpp_suffix = '''
  return EXIT_SUCCESS;
}
void reset(bool existing = true) {
  existing_window = existing;
  minimized = true;
  show_calls = foreground_calls = uri_dispatch_calls = 0;
  bounded_handoff_calls = 0;
  delivery_success = true;
  shown_window = foreground_window = nullptr;
  emitted_uri.clear();
}
bool restored_same_window(bool attended_handoff) {
  return !minimized && show_calls == 1 && foreground_calls == 1 &&
      shown_window == &main_window && foreground_window == &main_window &&
      uri_dispatch_calls == 0 && bounded_handoff_calls == (attended_handoff ? 1 : 0) &&
      (!attended_handoff || emitted_uri == "--quick_support");
}
int main() {
  const wchar_t unicode[] = {L'Z', static_cast<wchar_t>(0xfc), L'r', L'i', L'c', L'h',
      static_cast<wchar_t>(0xd83d), static_cast<wchar_t>(0xde42), L'Z', 0};
  const wchar_t invalid[] = {static_cast<wchar_t>(0xd800), 0};
  std::vector<std::string> args = native_arguments({L"--quick_support", L"--cm",
      L"mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-key-last-Z",
      L"", L" \\n\\r\\t", L"--quick_support \\n\\r\\t", L"--install", unicode,
      L" leading-preserved-final-Z \\t", nullptr, invalid});
  const std::vector<std::string> expected = {"--quick_support", "--cm",
      "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-key-last-Z",
      "", "", "--quick_support", "--install", "Z\\xc3\\xbcrich\\xf0\\x9f\\x99\\x82Z",
      " leading-preserved-final-Z", "", ""};
  for (std::size_t index = 0; index < 9; ++index) {
    if (args[index].empty() || args[index].back() != '\\0') return 10;
  }
  if (!args[9].empty() || !args[10].empty()) return 10;
#ifdef UPSTREAM_NORMALIZER
  args.resize(9); // The pinned original throws on invalid/null converter output.
#endif
  normalize(args);
#ifdef PREVIOUS_NORMALIZER
  if (args[0] != expected[0] && args[0].back() == '\\0' &&
      std::find(parameters_white_list.begin(), parameters_white_list.end(), args[1]) == parameters_white_list.end() &&
      std::find(parameters_white_list.begin(), parameters_white_list.end(), args[6]) == parameters_white_list.end()) {
    std::cerr << "REPRODUCED: pre-fix native converter NUL prevents exact QuickSupport/CM/install comparisons" << std::endl;
    return 11;
  }
  return 1;
#elif defined(UPSTREAM_NORMALIZER)
  if (args[0] == expected[0] && args[1] == expected[1] && args[2] == expected[2] &&
      args[6] == expected[6] && args[7] == expected[7] && args[3].empty() &&
      args[4] == " \\n\\r\\t" && args[5] == "--quick_support \\n\\r\\t") {
    std::cerr << "REPRODUCED: pinned original removes converter NUL but leaves trailing whitespace" << std::endl;
    return 12;
  }
  return 1;
#else
  if (args != expected) {
    std::cerr << "Generated Windows arguments changed token bytes or rejected empty input" << std::endl;
    return 1;
  }
  auto interior = std::vector<std::string>{std::string("A\\0B\\0", 4)};
  normalize(interior);
  if (interior[0] != std::string("A\\0B", 3)) return 13;
  std::cout << "PASS: exact pinned converter -> generated normalization (" << converter_backend_name()
      << ") removes only the terminal NUL, preserves final invite/key and UTF-8 bytes, leading/interior bytes, and safely trims empty/whitespace input" << std::endl;
  reset();
  if (existing_window_dispatch(native_arguments({L"--quick_support"})) != EXIT_FAILURE || !restored_same_window(true)) {
    std::cerr << "QuickSupport warm dispatch did not restore the same minimized HWND; URI transport emitted: " << emitted_uri << std::endl;
    return 2;
  }
  reset();
  if (existing_window_dispatch({}) != EXIT_FAILURE || !restored_same_window(false)) return 3;
  const std::string invite = args[2];
  for (const auto* uri : {L"mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-key-last-Z", L"mixel-remote://123456789"}) {
    reset();
    auto expected_uri = native_arguments({uri});
    normalize(expected_uri);
    if (existing_window_dispatch(native_arguments({uri})) != EXIT_FAILURE || uri_dispatch_calls != 1 ||
        emitted_uri != expected_uri[0] || show_calls != 0 || foreground_calls != 0 ||
        bounded_handoff_calls != 0 || !minimized) return 4;
  }
  for (const auto& arguments : {native_arguments({L"--cm"}),
                               native_arguments({L"--install"}),
                               native_arguments({L"--quick_support", L"--cm"})}) {
    reset();
    if (existing_window_dispatch(arguments) != EXIT_SUCCESS || uri_dispatch_calls != 0 ||
        show_calls != 0 || foreground_calls != 0 || bounded_handoff_calls != 0) return 5;
  }
  reset(false);
  if (existing_window_dispatch(native_arguments({L"--quick_support"})) != EXIT_SUCCESS || uri_dispatch_calls != 0 ||
      show_calls != 0 || foreground_calls != 0 || bounded_handoff_calls != 0) return 6;
  reset();
  delivery_success = false;
  if (existing_window_dispatch(native_arguments({L"--quick_support"})) != EXIT_SUCCESS || !restored_same_window(true)) return 7;
  reset();
  if (existing_window_dispatch(native_arguments({L"--quick_support"}), {"--mixel-attended-handoff-unavailable"}) != EXIT_SUCCESS || !restored_same_window(false)) return 8;
  reset();
  if (existing_window_dispatch({invite}, {"--mixel-attended-handoff-unavailable"}) != EXIT_SUCCESS || uri_dispatch_calls != 0 || bounded_handoff_calls != 0) return 9;
  std::cout << "PASS: actual generated Windows branch restores the same QuickSupport HWND and sends exact bounded pinned-plugin attended intent; failed delivery falls through to cold owned GUI; URI/CM/install/cold paths remain intact" << std::endl;
  std::cout << "PASS: failed attended IPC acknowledgment retains the cold foreground owner before asynchronous URI/QuickSupport delivery" << std::endl;
#endif
}
'''
    # Compile the actual unpatched dispatch first. This recreates the native
    # failure: the plugin gets a relative URI flag and never restores the HWND.
    (cpp / "original.cpp").write_text(cpp_prefix + upstream_dispatch + cpp_suffix, encoding="utf-8")
    (cpp / "main.cpp").write_text(cpp_prefix + dispatch + cpp_suffix, encoding="utf-8")
    (cpp / "previous_normalizer.cpp").write_text("#define PREVIOUS_NORMALIZER\n" + cpp_prefix.replace(normalize, previous_normalize, 1) + dispatch + cpp_suffix, encoding="utf-8")
    (cpp / "upstream_normalizer.cpp").write_text("#define UPSTREAM_NORMALIZER\n" + cpp_prefix.replace(normalize, upstream_normalize, 1) + dispatch + cpp_suffix, encoding="utf-8")
    cpp_build = cpp / "build"
    run(["cmake", "-S", str(cpp), "-B", str(cpp_build)])
    run(["cmake", "--build", str(cpp_build), "--config", "Release"])
    binaries = [cpp_build / "window_arguments", cpp_build / "Release/window_arguments.exe", cpp_build / "window_arguments.exe"]
    binary = next((candidate for candidate in binaries if candidate.is_file()), None)
    if binary is None:
        raise RuntimeError("CMake did not build the generated Windows argument regression executable")
    original_binary = binary.with_name("window_arguments_original" + binary.suffix)
    if not original_binary.is_file():
        raise RuntimeError("CMake did not build the original Windows dispatch regression executable")
    negative = subprocess.run([str(original_binary)], capture_output=True, text=True, encoding="utf-8")
    assert negative.returncode == 2 and "URI transport emitted: --quick_support" in negative.stderr, "Original Windows dispatch must reproduce the real QuickSupport warm failure"
    print("PASS: actual original Windows dispatch reproduces QuickSupport relative-URI failure before the native restore fix")
    for suffix, code, message in (
            ("previous_normalizer", 11, "pre-fix native converter NUL prevents exact QuickSupport/CM/install comparisons"),
            ("upstream_normalizer", 12, "pinned original removes converter NUL but leaves trailing whitespace")):
        control = binary.with_name("window_arguments_" + suffix + binary.suffix)
        negative = subprocess.run([str(control)], capture_output=True, text=True, encoding="utf-8")
        assert negative.returncode == code and message in negative.stderr, "Actual converter normalization control did not reproduce: " + suffix
        print("PASS: actual converter negative control: " + message)
    print(run([str(binary)]).stdout.strip())
    # Also qualify upgrading the already-branded pre-fix source, rather than
    # allowing a repeated application to retain its NUL comparison regression.
    windows_main_path = repo / "flutter/windows/runner/main.cpp"
    windows_main_path.write_text(windows_main.replace(sentinel_guard, "", 1), encoding="utf-8")
    run(BRANDING_COMMAND, cwd=base, env=env)
    assert windows_main_path.read_text(encoding="utf-8") == windows_main
    print("PASS: full branding upgrades the pre-fix NUL-retaining normalizer to the exact fresh generated source")

    # Execute the actual package generator, including its architecture-specific
    # dependencies, without importing the build script's command-line driver.
    expected_depends = ["libgtk-3-0", "libegl1", "libgl1", "libgles2", "libgl1-mesa-dri",
                        "libxcb-randr0", "libxdo3 | libxdo4", "libxfixes3", "libxcb-shape0",
                        "libxcb-xfixes0", "libasound2", "libsystemd0", "curl", "libva2",
                        "libva-drm2", "libva-x11-2", "libgstreamer-plugins-base1.0-0",
                        "libpam0g", "gstreamer1.0-pipewire"]
    # The real Linux generator uses open(..., "w") with the host default.
    # Run that fixture with Linux's UTF-8 text semantics even when this test's
    # parent is native Windows/CP1252. Keep strict UTF-8 decoding; never repair
    # invalid package bytes by replacing or silently dropping characters.
    debian_probe = '''import ast
import json
import os
from pathlib import Path
import sys

assert os.environ.get("PYTHONUTF8") == "1", "Debian fixture child must explicitly enable UTF-8"
assert sys.flags.utf8_mode == 1, "Debian fixture must run in explicit UTF-8 mode"
repo = Path(sys.argv[1])
expected_depends = json.loads(sys.argv[2])
source = (repo / "build.py").read_text(encoding="utf-8")
functions = ast.Module(body=[node for node in ast.parse(source).body
                            if isinstance(node, ast.FunctionDef) and node.name in
                            {"generate_control_file", "get_deb_arch", "get_deb_extra_depends"}],
                       type_ignores=[])
generator = {"os": os, "system2": lambda _command: None}
exec(compile(functions, str(repo / "build.py"), "exec"), generator)
os.chdir(repo / "flutter")
for arch in ("amd64", "arm64", "armhf"):
    os.environ["DEB_ARCH"] = arch
    generator["generate_control_file"]("1.4.6")
    control_path = repo / "res/DEBIAN/control"
    with open(control_path) as stream:
        assert stream.encoding.lower().replace("-", "") == "utf8", "Generator child default text IO is not UTF-8"
    control = control_path.read_text(encoding="utf-8")
    fields = dict(line.split(": ", 1) for line in control.splitlines() if ": " in line)
    dependencies = fields["Depends"].split(", ")
    assert dependencies == expected_depends + (["libatomic1"] if arch == "armhf" else [])
    assert len(dependencies) == len(set(dependencies)), "duplicate package dependency"
    assert fields["Package"] == "mixel-remote" and fields["Architecture"] == arch
    assert fields["Description"] == "Mixel Remote — remote support client by Mixel IT and Corporate Services GmbH.", "Package description was damaged by text encoding"
print("PASS: actual Debian generator child explicitly uses UTF-8 and preserves the full branded description")
'''
    generated = run([sys.executable, "-c", debian_probe, str(repo), json.dumps(expected_depends)],
                    env={**env, "PYTHONUTF8": "1"})
    print(generated.stdout.strip())
    print("PASS: actual Debian control generator requires EGL, GL, GLES and Mesa software rendering on all package architectures")

    # Execute the generated Debian upgrade path against an isolated filesystem.
    # Reproduces a legacy /etc service with missing /usr/lib unit copies; every
    # filesystem operation is redirected into this disposable directory.
    if os.name != "nt":
        sandbox = base / "package-root"
        for path in ("proc/1", "etc/systemd/system", "usr/bin", "usr/share/mixel-remote/files/systemd", "commands"):
            (sandbox / path).mkdir(parents=True, exist_ok=True)
        (sandbox / "proc/1/exe").symlink_to("/fixture/systemd")
        (sandbox / "etc/systemd/system/mixel-remote.service").write_text("legacy override", encoding="utf-8")
        (sandbox / "usr/share/mixel-remote/files/systemd/mixel-remote.service").write_text("fixture service", encoding="utf-8")
        systemctl = sandbox / "commands/systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        systemctl.chmod(0o755)
        maintainer = (repo / "res/DEBIAN/postinst").read_text(encoding="utf-8")
        for prefix in ("/proc/", "/etc/", "/usr/"):
            maintainer = maintainer.replace(prefix, str(sandbox) + prefix)
        fixture = sandbox / "postinst"
        fixture.write_text(maintainer, encoding="utf-8")
        package_env = {**env, "PATH": str(sandbox / "commands") + os.pathsep + env.get("PATH", "")}
        run([BASH, str(fixture), "configure"], env=package_env)
        assert not (sandbox / "etc/systemd/system/mixel-remote.service").exists()
        assert (sandbox / "usr/lib/systemd/system/mixel-remote.service").read_text(encoding="utf-8") == "fixture service"
        assert (sandbox / "usr/bin/mixel-remote").is_symlink()
        print("PASS: actual generated Debian postinst upgrades legacy service with missing unit copies")
    else:
        print("SKIP: Debian maintainer execution fixture requires native Unix filesystem semantics")

    # Execute the real ICNS fallback even on hosts with iconutil. ImageMagick
    # otherwise embeds its current clock in the internal PNG, so two complete
    # branding runs produce different bytes on Windows and Linux.
    magick = subprocess.run([BASH, "-c", "command -v magick >/dev/null 2>&1"],
                            cwd=base, env=env, capture_output=True)
    if magick.returncode == 0:
        branding_source = SCRIPT.read_text(encoding="utf-8")
        macos_icons = branding_source[branding_source.index('# macOS .icns'):branding_source.index('# 3. Patch user-visible')]
        fallback = macos_icons.split('  elif command -v magick >/dev/null 2>&1; then\n', 1)[1].split('  else\n', 1)[0]
        icns = base / "fallback-icon.icns"
        fallback_env = {**env, "MACOS_ICNS": icns.as_posix()}
        run([BASH, "-c", fallback], cwd=base, env=fallback_env)
        first_icns = icns.read_bytes()
        time.sleep(1.1)
        run([BASH, "-c", fallback], cwd=base, env=fallback_env)
        assert icns.read_bytes() == first_icns, "ImageMagick ICNS fallback embeds changing metadata"
        print("PASS: actual ImageMagick ICNS fallback remains byte-identical across distinct output timestamps")
    else:
        print("SKIP: ImageMagick ICNS fallback execution requires magick")

    before = snapshot(repo)
    run(BRANDING_COMMAND, cwd=base, env=env)
    after = snapshot(repo)
    changed = [path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]
    assert not changed, "complete branding must be idempotent; byte details: " + json.dumps(
        {path: {"first": before.get(path), "second": after.get(path)} for path in changed}, sort_keys=True)
    print("PASS: the complete branding pipeline is byte-for-byte idempotent")

    cases = [
        ("flutter/linux/main.cc", '#define RUSTDESK_LIB_PATH "libmixel-remote.so"', '#define RUSTDESK_LIB_PATH "wrong.so"', "Linux runner loader"),
        ("flutter/windows/runner/main.cpp", 'LoadLibraryA("libmixel-remote.dll")', 'LoadLibraryA("wrong.dll")', "Windows runner loader"),
        ("flutter/macos/Runner/Info.plist", '<string>mixel-remote</string>', '<string>missing-protocol</string>', "macOS URL handler"),
        ("libs/hbb_common/src/config.rs", '("relay-server".to_owned(), "rs.mixel.ch".to_owned())', '("relay-server".to_owned(), "wrong.example".to_owned())', "visible relay default"),
    ]
    for path, expected, corrupted, label in cases:
        file = repo / path
        original = file.read_text(encoding="utf-8")
        assert expected in original
        file.write_text(original.replace(expected, corrupted), encoding="utf-8")
        rejected = subprocess.run(BRANDING_COMMAND, cwd=base, env=env, capture_output=True, text=True, encoding="utf-8")
        file.write_text(original, encoding="utf-8")
        assert rejected.returncode != 0, f"Corrupt {label} unexpectedly passed"
        assert f"Required runtime branding missing in {path}" in rejected.stderr, rejected.stderr[-4000:]
        print(f"PASS: full branding rejects a corrupt {label}")

print("PASS: all full-pipeline branding regression checks")
