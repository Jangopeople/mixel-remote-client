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


def snapshot(repo: Path) -> dict[str, str]:
    result = {}
    for source in (repo, repo / "libs/hbb_common", repo / ".mixel-deps/rdev", repo / ".mixel-deps/clipboard-master"):
        paths = run(["git", "-C", str(source), "ls-files", "--cached", "--others", "--exclude-standard"]).stdout.splitlines()
        for path in paths:
            target = source / path
            if target.is_file():
                result[str(target.relative_to(repo))] = hashlib.sha256(target.read_bytes()).hexdigest()
    return result


with tempfile.TemporaryDirectory(prefix="mixel-branding-") as directory:
    base = Path(directory)
    repo = base / "rustdesk"
    run(["git", "clone", "--quiet", "--shared", "--branch", "1.4.6", str(SOURCE), str(repo)])
    run(["git", "clone", "--quiet", "--shared", str(SOURCE / "libs/hbb_common"), str(repo / "libs/hbb_common")])
    submodule_commit = run(["git", "-C", str(repo), "ls-tree", "HEAD", "libs/hbb_common"]).stdout.split()[2]
    run(["git", "-C", str(repo / "libs/hbb_common"), "checkout", "--quiet", submodule_commit])
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
    print("PASS: branded GTK icon/titles, isolated Linux DBus, bundled library strip and macOS protocol registration")

    # Execute the actual generated C++ argument normalization. Keeping the
    # last character is essential for support invite/API keys and the portable
    # --quick_support handoff; empty/whitespace-only arguments must not throw.
    windows_main = (repo / "flutter/windows/runner/main.cpp").read_text(encoding="utf-8")
    normalize = windows_main[windows_main.index("  // Remove possible trailing whitespace"):windows_main.index("\n\n  int args_len")]
    cpp = base / "windows-arguments"
    cpp.mkdir()
    (cpp / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.16)
project(mixel_window_arguments LANGUAGES CXX)
add_executable(window_arguments main.cpp)
target_compile_features(window_arguments PRIVATE cxx_std_17)
if(MSVC)
  target_compile_options(window_arguments PRIVATE /W4 /WX)
else()
  target_compile_options(window_arguments PRIVATE -Wall -Wextra -Werror)
endif()
''', encoding="utf-8")
    (cpp / "main.cpp").write_text('''#include <iostream>
#include <string>
#include <vector>
void normalize(std::vector<std::string>& command_line_arguments) {
''' + normalize + '''
}
int main() {
  std::vector<std::string> args = {"--quick_support", "--cm", "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-key-last-Z", "", " \\n\\r\\t", "--quick_support \\n\\r\\t"};
  const auto original = args;
  normalize(args);
  if (args[0] != original[0] || args[1] != original[1] || args[2] != original[2] || !args[3].empty() || !args[4].empty() || args[5] != "--quick_support") {
    std::cerr << "Generated Windows arguments changed token bytes or rejected empty input" << std::endl;
    return 1;
  }
  std::cout << "PASS: actual generated Windows C++ argument normalization preserves complete QuickSupport/invite/key bytes and accepts empty/whitespace input" << std::endl;
}
''', encoding="utf-8")
    cpp_build = cpp / "build"
    run(["cmake", "-S", str(cpp), "-B", str(cpp_build)])
    run(["cmake", "--build", str(cpp_build), "--config", "Release"])
    binaries = [cpp_build / "window_arguments", cpp_build / "Release/window_arguments.exe", cpp_build / "window_arguments.exe"]
    binary = next((candidate for candidate in binaries if candidate.is_file()), None)
    if binary is None:
        raise RuntimeError("CMake did not build the generated Windows argument regression executable")
    print(run([str(binary)]).stdout.strip())

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

    before = snapshot(repo)
    run(BRANDING_COMMAND, cwd=base, env=env)
    assert snapshot(repo) == before, "complete branding must be idempotent"
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
