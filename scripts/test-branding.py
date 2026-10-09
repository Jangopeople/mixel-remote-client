#!/usr/bin/env python3
"""Run the complete branding pipeline on a fresh pinned source clone."""
import ast
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("RDREPO", ROOT / "rustdesk")).resolve()
SCRIPT = ROOT / "scripts/apply-branding.sh"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command[0]}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result


def snapshot(repo: Path) -> dict[str, str]:
    result = {}
    for source in (repo, repo / "libs/hbb_common", repo / ".mixel-deps/rdev"):
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
    env["BRANDING"] = str(ROOT / "branding")
    first = run(["bash", str(SCRIPT)], cwd=base, env=env)
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
    plist = plistlib.loads((repo / "flutter/macos/Runner/Info.plist").read_bytes())
    assert plist["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["mixel-remote"]
    print("PASS: branded GTK icon/titles, isolated Linux DBus, bundled library strip and macOS protocol registration")

    # Execute the actual package generator, including its architecture-specific
    # dependencies, without importing the build script's command-line driver.
    functions = ast.Module(body=[node for node in ast.parse(build).body
                                if isinstance(node, ast.FunctionDef) and node.name in
                                {"generate_control_file", "get_deb_arch", "get_deb_extra_depends"}],
                           type_ignores=[])
    generator = {"os": os, "system2": lambda _command: None}
    exec(compile(functions, str(repo / "build.py"), "exec"), generator)
    expected_depends = ["libgtk-3-0", "libegl1", "libgl1", "libgles2", "libgl1-mesa-dri",
                        "libxcb-randr0", "libxdo3 | libxdo4", "libxfixes3", "libxcb-shape0",
                        "libxcb-xfixes0", "libasound2", "libsystemd0", "curl", "libva2",
                        "libva-drm2", "libva-x11-2", "libgstreamer-plugins-base1.0-0",
                        "libpam0g", "gstreamer1.0-pipewire"]
    original_directory, original_arch = Path.cwd(), os.environ.get("DEB_ARCH")
    try:
        os.chdir(repo / "flutter")
        for arch in ("amd64", "arm64", "armhf"):
            os.environ["DEB_ARCH"] = arch
            generator["generate_control_file"]("1.4.6")
            control = (repo / "res/DEBIAN/control").read_text(encoding="utf-8")
            fields = dict(line.split(": ", 1) for line in control.splitlines() if ": " in line)
            dependencies = fields["Depends"].split(", ")
            assert dependencies == expected_depends + (["libatomic1"] if arch == "armhf" else [])
            assert len(dependencies) == len(set(dependencies)), "duplicate package dependency"
            assert fields["Package"] == "mixel-remote" and fields["Architecture"] == arch
    finally:
        os.chdir(original_directory)
        if original_arch is None:
            os.environ.pop("DEB_ARCH", None)
        else:
            os.environ["DEB_ARCH"] = original_arch
    print("PASS: actual Debian control generator requires EGL, GL, GLES and Mesa software rendering on all package architectures")

    # Execute the generated Debian upgrade path against an isolated filesystem.
    # Reproduces a legacy /etc service with missing /usr/lib unit copies; every
    # filesystem operation is redirected into this disposable directory.
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
    run(["bash", str(fixture), "configure"], env=package_env)
    assert not (sandbox / "etc/systemd/system/mixel-remote.service").exists()
    assert (sandbox / "usr/lib/systemd/system/mixel-remote.service").read_text(encoding="utf-8") == "fixture service"
    assert (sandbox / "usr/bin/mixel-remote").is_symlink()
    print("PASS: actual generated Debian postinst upgrades legacy service with missing unit copies")

    before = snapshot(repo)
    run(["bash", str(SCRIPT)], cwd=base, env=env)
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
        rejected = subprocess.run(["bash", str(SCRIPT)], cwd=base, env=env, capture_output=True, text=True, encoding="utf-8")
        file.write_text(original, encoding="utf-8")
        assert rejected.returncode != 0, f"Corrupt {label} unexpectedly passed"
        assert f"Required runtime branding missing in {path}" in rejected.stderr, rejected.stderr[-4000:]
        print(f"PASS: full branding rejects a corrupt {label}")

print("PASS: all full-pipeline branding regression checks")
