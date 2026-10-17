#!/usr/bin/env python3
"""Prove pinned analyzer fixes and unchanged Client/terminal behavior."""
import argparse

import ast
import collections
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
input_lifecycle = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(input_lifecycle)

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("mixel_flutter_analysis",ROOT/"scripts/patch-support-flutter-analysis.py")
P=importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
termination_spec=importlib.util.spec_from_file_location("mixel_application_exit_composition",ROOT/"scripts/patch-support-macos-termination.py")
T=importlib.util.module_from_spec(termination_spec)
termination_spec.loader.exec_module(T)

sdk_spec=importlib.util.spec_from_file_location("mixel_sdk_compatibility_composition",ROOT/"scripts/patch-support-flutter-sdk-compatibility.py")
S=importlib.util.module_from_spec(sdk_spec)
sdk_spec.loader.exec_module(S)

rdev_spec=importlib.util.spec_from_file_location("mixel_rdev_fixture_profiles",ROOT/"scripts/patch-support-macos-rdev.py")
R=importlib.util.module_from_spec(rdev_spec)
rdev_spec.loader.exec_module(R)

file_transfer_spec=importlib.util.spec_from_file_location("mixel_file_transfer_composition",ROOT/"scripts/patch-support-file-transfer-window.py")
F=importlib.util.module_from_spec(file_transfer_spec)
file_transfer_spec.loader.exec_module(F)


def copy_current_pipeline_inputs(destination):
    """Bind every current tracked/incoming fixture resource into private copies.

    Git selects scripts/branding inputs; generated caches and ignored runtime
    files are excluded. HEAD's archive remains the baseline, then these exact
    regular current files supply the reviewed integration under test.
    """
    names = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "scripts", "branding"])
    for raw_name in sorted(set(names.decode("utf-8").split("\0")) - {""}):
        relative = Path(raw_name)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in ("scripts", "branding"):
            raise RuntimeError("Unsafe current fixture input name")
        source = ROOT / relative
        status = source.lstat()
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_size > 10_000_000 or getattr(status, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Unowned current fixture input")
        for parent in source.parents:
            if parent == ROOT: break
            entry = parent.lstat()
            if not stat.S_ISDIR(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("Unsafe current fixture input parent")
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


@contextlib.contextmanager
def linux_close_baseline(source, _input_checked=False):
    """Expose the exact previous owners without removing startup under test.

    Validate each supplied late Linux layer before its private inverse copies
    anything. The product helper pins and original input remain unchanged.
    """
    source = Path(source).absolute()
    if not _input_checked:
        with input_lifecycle.current_source(source) as previous:
            with linux_close_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    source = source.resolve()
    uri_spec = importlib.util.spec_from_file_location("mixel_linux_close_baseline_uri", ROOT / "scripts/patch-support-uri-routing.py")
    uri = importlib.util.module_from_spec(uri_spec)
    uri_spec.loader.exec_module(uri)
    if uri.has_layer(source):
        with uri.previous(source) as previous:
            with linux_close_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    native_spec = importlib.util.spec_from_file_location("mixel_historical_native_close", ROOT / "scripts/patch-support-linux-child-window-close.py")
    native_close = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native_close)
    if native_close.has_layer(source):
        with native_close.previous(source) as previous:
            with linux_close_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    linux_spec = importlib.util.spec_from_file_location("mixel_historical_linux_close", ROOT / "scripts/patch-support-linux-window-close.py")
    linux_close = importlib.util.module_from_spec(linux_spec)
    linux_spec.loader.exec_module(linux_close)
    if linux_close.has_layer(source):
        with linux_close.previous(source) as previous:
            with linux_close_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    yield source


@contextlib.contextmanager
def startup_baseline(source, _input_checked=False):
    """Verify earlier layers in a private exact startup inverse; input is read-only.

    The final startup suite qualifies its actual generated bodies separately.
    This projection never accepts an unpinned input or changes supplied source.
    """
    source = Path(source).absolute()
    if not _input_checked:
        with input_lifecycle.current_source(source) as previous:
            with startup_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    source = source.resolve()
    uri_spec = importlib.util.spec_from_file_location("mixel_startup_baseline_uri", ROOT / "scripts/patch-support-uri-routing.py")
    uri = importlib.util.module_from_spec(uri_spec)
    uri_spec.loader.exec_module(uri)
    if uri.has_layer(source):
        with uri.previous(source) as previous:
            with startup_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    native_spec = importlib.util.spec_from_file_location("mixel_fixture_linux_child_close", ROOT / "scripts/patch-support-linux-child-window-close.py")
    native_close = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native_close)
    if native_close.has_layer(source):
        with native_close.previous(source) as previous:
            with startup_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    linux_spec = importlib.util.spec_from_file_location("mixel_fixture_linux_close", ROOT / "scripts/patch-support-linux-window-close.py")
    linux_close = importlib.util.module_from_spec(linux_spec)
    linux_spec.loader.exec_module(linux_close)
    if linux_close.has_layer(source):
        with linux_close.previous(source) as previous:
            with startup_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    launch_spec = importlib.util.spec_from_file_location("mixel_fixture_launch", ROOT / "scripts/patch-support-macos-gui-launch.py")
    launch = importlib.util.module_from_spec(launch_spec)
    launch_spec.loader.exec_module(launch)
    if launch.has_layer(source):
        with launch.previous(source) as previous:
            with startup_baseline(previous, _input_checked=True) as checked:
                yield checked
        return
    spec = importlib.util.spec_from_file_location("mixel_fixture_startup", ROOT / "scripts/patch-macos-startup.py")
    startup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(startup)
    compiler_spec = importlib.util.spec_from_file_location("mixel_fixture_compiler", ROOT / "scripts/patch-support-compiler-cleanup.py")
    compiler = importlib.util.module_from_spec(compiler_spec)
    compiler_spec.loader.exec_module(compiler)
    has_compiler = any((source/name).is_file() and hashlib.sha256((source/name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected
                       for name, expected in compiler.CORRECTED.items())
    original = {name: (source / name).read_bytes() for name in startup.INPUT_HASHES}
    original["src/tray.rs"] = (source / "src/tray.rs").read_bytes()
    if has_compiler:
        # Validate actual supplied ownership before a copy can hide inventory
        # or regularize a disjoint source link; this gate performs no writes.
        assert compiler.apply(source,check=True,restore=True,before_branding=True)['writes']==0
        original.update({name: (source/name).read_bytes() for name in compiler.ORIGINAL})
    if not has_compiler and not any(startup.MARKER.encode() in value for value in original.values()):
        yield source
        return
    with tempfile.TemporaryDirectory(prefix="mixel-exact-startup-inverse-") as temporary:
        projected = Path(temporary).resolve() / "source"
        ignore=((lambda directory,names: compiler.projection_ignored(source,directory,names) | {".git"})
                if has_compiler else shutil.ignore_patterns(".git", "build", ".dart_tool", "__pycache__"))
        shutil.copytree(source, projected, symlinks=True,ignore=ignore)
        if has_compiler:
            compiler.apply(projected, restore=True)
        startup.apply(projected, restore=True)
        tray_spec = importlib.util.spec_from_file_location("mixel_fixture_tray", ROOT / "scripts/patch-support-macos-tray-url.py")
        tray = importlib.util.module_from_spec(tray_spec)
        tray_spec.loader.exec_module(tray)
        tray.apply(projected, restore=True)
        assert {name: startup.digest((projected / name).read_text(encoding="utf-8")) for name in startup.INPUT_HASHES} == startup.INPUT_HASHES
        try:
            yield projected
        finally:
            assert {name: (source / name).read_bytes() for name in original} == original, "Startup projection mutated supplied input"


def run(args,out,label,*,cwd=None,env=None,success=True,full_branding=False):
    # The parent decoder cannot configure a captured Python child's encoder.
    # Match both sides of this fixture's pipe without changing file encodings,
    # the caller's environment, or the real build's environment.
    child_env=dict(os.environ if env is None else env,PYTHONIOENCODING="utf-8")
    timeout=600 if full_branding and os.name=="nt" else 240
    started=time.monotonic()
    try:
        result=subprocess.run(args,cwd=cwd,env=child_env,capture_output=True,text=True,encoding="utf-8",timeout=timeout)
    except subprocess.TimeoutExpired as error:
        stdout=error.stdout if isinstance(error.stdout,bytes) else (error.stdout or "").encode("utf-8")
        stderr=error.stderr if isinstance(error.stderr,bytes) else (error.stderr or "").encode("utf-8")
        (out/(label+".stdout.log")).write_bytes(stdout)
        (out/(label+".stderr.log")).write_bytes(stderr)
        (out/(label+".log")).write_bytes(stdout+stderr)
        (out/(label+".timing.json")).write_text(json.dumps({
            "status":"timed-out","timeout_seconds":timeout,
            "elapsed_seconds":round(time.monotonic()-started,3),
            "full_branding":full_branding,"stdout_bytes":len(stdout),"stderr_bytes":len(stderr)
        },sort_keys=True,indent=2)+"\n",encoding="utf-8")
        stdout_tail=stdout[-4096:].decode("utf-8",errors="replace").encode("ascii",errors="backslashreplace").decode("ascii")[-4096:]
        stderr_tail=stderr[-4096:].decode("utf-8",errors="replace").encode("ascii",errors="backslashreplace").decode("ascii")[-4096:]
        diagnostic=("TIMEOUT: "+label+" after "+str(timeout)+" seconds; captured stdout/stderr retained.\n"
                    +"stdout tail:\n"+stdout_tail+"\nstderr tail:\n"+stderr_tail)
        print(diagnostic.encode("ascii",errors="backslashreplace").decode("ascii"),flush=True)
        raise
    (out/(label+".log")).write_text(result.stdout+result.stderr,encoding="utf-8")
    (out/(label+".timing.json")).write_text(json.dumps({
        "status":"completed","timeout_seconds":timeout,
        "elapsed_seconds":round(time.monotonic()-started,3),
        "full_branding":full_branding,"returncode":result.returncode
    },sort_keys=True,indent=2)+"\n",encoding="utf-8")
    if success:
        assert result.returncode==0,result.stdout+result.stderr
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(repo):
    result={}
    for parent,dirs,names in os.walk(repo):
        dirs[:]=[name for name in dirs if name not in (".git","__pycache__",".dart_tool","build")]
        for name in names:
            if name==".git":
                continue
            path=Path(parent)/name
            result[path.relative_to(repo).as_posix()]=("symlink:"+os.readlink(path)
                if path.is_symlink() else sha(path.read_bytes()))
    return result


def pinned(source,name):
    return subprocess.check_output(["git","-C",str(source),"show",P.PIN+":"+name],timeout=60)


def branding_bash(explicit,windows):
    if explicit is None:
        if windows:
            raise RuntimeError("Windows analyzer fixture requires the active Git Bash executable by full path")
        return "bash"
    if windows and not explicit.is_absolute():
        raise RuntimeError("Windows analyzer fixture requires an absolute active Git Bash path")
    resolved=explicit.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError("Analyzer fixture Bash executable is not a file")
    if windows:
        # The dispatcher supplies its actual running BASH, converted by that
        # Git installation's cygpath. Refuse the WSL/System32 stub before
        # execution, and require the adjacent public Git/MSYS installation.
        if resolved.name.lower()!="bash.exe" or resolved.parent.name.lower()!="bin":
            raise RuntimeError("Windows analyzer fixture requires Git Bash, not WSL")
        install=resolved.parent.parent
        if install.name.lower()=="usr":
            install=install.parent
        if not (install/"cmd/git.exe").is_file() or not (install/"usr/bin/cygpath.exe").is_file():
            raise RuntimeError("Active Windows Bash is not in the installed Git/MSYS layout")
    return str(resolved)


def git_bash_runtime(selected,output):
    # Query the exact selected shell's own public MSYS path mapper. GNU Bash
    # version text is informational and does not identify its Windows runtime.
    shell=PureWindowsPath(selected)
    install=shell.parent.parent
    if install.name.lower()=="usr":
        install=install.parent
    lines=output.splitlines()
    if len(lines)!=2:
        raise RuntimeError("Active Git Bash runtime query must return exactly two Windows paths")
    root,actual=(PureWindowsPath(line) for line in lines)
    if not root.is_absolute() or not actual.is_absolute() or root!=install:
        raise RuntimeError("Active Bash runtime does not map to the supplied Git installation")
    if actual not in tuple(install/name for name in (
            "bin/bash","bin/bash.exe","usr/bin/bash","usr/bin/bash.exe")):
        raise RuntimeError("Active Bash runtime executable is outside the supplied Git installation")
    return {"installation":str(root),"running_bash":str(actual)}


def shell_controls(out,bash,source):
    control=out/"shell préflight 🚀"
    control.mkdir()
    git=control/"Git with spaces"
    for name in ("cmd/git.exe","usr/bin/cygpath.exe","bin/bash.exe","usr/bin/bash.exe"):
        path=git/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b"source-only installation layout control")
    for name in ("bin/bash.exe","usr/bin/bash.exe"):
        path=git/name
        assert branding_bash(path,True)==str(path.resolve())
    system=control/"Windows/System32/bash.exe"
    system.parent.mkdir(parents=True)
    system.write_bytes(b"source-only WSL path control")
    refusals=0
    for path in (None,Path("bash"),system,git,control/"absent.exe"):
        try:
            branding_bash(path,True)
        except (RuntimeError,OSError):
            refusals+=1
        else:
            raise AssertionError("Missing/relative/WSL/nonfile shell path was accepted")
    (git/"cmd/git.exe").unlink()
    for name in ("bin/bash.exe","usr/bin/bash.exe"):
        try:
            branding_bash(git/name,True)
        except RuntimeError:
            refusals+=1
        else:
            raise AssertionError("Non-Git Bash layout was accepted")
    assert refusals==7
    assert branding_bash(None,False)=="bash","Existing Unix shell default changed"
    selected=Path(bash if bash!="bash" else shutil.which("bash")).resolve(strict=True)
    assert branding_bash(selected,False)==str(selected)
    # Run the actual supplied native shell with a spaced Unicode script,
    # argument and environment. Layout stand-ins above are never executed.
    script=control/"actual shell invocation préflight.sh"
    script.write_text('set -euo pipefail\n'+
        '[ "$1" = "préflight 🚀" ]\n'+
        '[ "$RDREPO" = "$2" ]\n'+
        '[ "$BRANDING" = "$3" ]\n'+
        'printf "%s\\n" "PASS: actual exact Bash preserves préflight 🚀 argument and shell-facing paths"\n',
        encoding="utf-8",newline="\n")
    env=dict(os.environ,RDREPO=(control/"source préflight").as_posix(),
        BRANDING=(control/"branding préflight").as_posix())
    result=run([str(selected),script.as_posix(),"préflight 🚀",env["RDREPO"],env["BRANDING"]],
        out,"actual-exact-bash-unicode-control",env=env)
    assert "PASS: actual exact Bash preserves préflight 🚀 argument and shell-facing paths" in result.stdout
    version=run([str(selected),"--version"],out,"actual-exact-bash-version")
    print("Actual exact Bash version (informational):\n"+version.stdout,flush=True)
    runtime=None
    # These are parser controls, not executions of the installation stand-ins.
    expected=r"C:\Program Files\Git"
    for name in ("bin/bash.exe","usr/bin/bash.exe"):
        for active in (r"C:\Program Files\Git\bin\bash",r"C:\Program Files\Git\bin\bash.exe",
                       r"C:\Program Files\Git\usr\bin\bash",r"C:\Program Files\Git\usr\bin\bash.exe"):
            assert git_bash_runtime(str(PureWindowsPath(expected)/name),expected+"\r\n"+active+"\r\n")["installation"]==expected
    runtime_refusals=0
    for value in ("",expected+"\n",expected+"\na\nb\n","relative\n"+expected+r"\bin\bash.exe",
                  r"C:\foreign\Git"+"\n"+expected+r"\bin\bash.exe",
                  expected+"\n"+r"C:\foreign\Git\usr\bin\bash.exe",
                  expected+"\n"+expected+r"\usr\bin\sh.exe"):
        try:
            git_bash_runtime(expected+r"\bin\bash.exe",value)
        except RuntimeError:
            runtime_refusals+=1
        else:
            raise AssertionError("Missing/relative/foreign Git Bash runtime path was accepted")
    assert runtime_refusals==7
    # The old assertion can reject a legitimate runtime solely because its
    # informational version output omits "msys". This text is a labelled mock;
    # the next Windows execution retains the actual version and runtime bytes.
    try:
        assert "msys" in "GNU bash, version 5.2.37(1)-release\n".lower(),"Actual active Windows shell is not Git/MSYS Bash"
    except AssertionError as error:
        assert str(error)=="Actual active Windows shell is not Git/MSYS Bash"
    else:
        raise AssertionError("Original version-substring false refusal was not reproduced")
    if os.name=="nt":
        queried=run([str(selected),"--noprofile","--norc","-c",
            '/usr/bin/cygpath.exe -w / && /usr/bin/cygpath.exe -w "$BASH"'],
            out,"actual-exact-bash-runtime")
        print("Actual exact Bash runtime paths:\n"+queried.stdout,flush=True)
        runtime=git_bash_runtime(str(selected),queried.stdout)
    tree=ast.parse(Path(__file__).read_text(encoding="utf-8"))
    owners = {"pipeline", "common_drift_controls", "prior_uri_common_drift_controls", "uri_common_drift_controls"}
    pipeline_nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in owners]
    assert {node.name for node in pipeline_nodes} == owners
    shell_runs=[call for node in pipeline_nodes for call in ast.walk(node)
        if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=="run"
        and call.args and isinstance(call.args[0],ast.List)
        and isinstance(call.args[0].elts[0],ast.Name) and call.args[0].elts[0].id=="bash"]
    by_owner = {node.name: sum(isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=="run"
                    and bool(call.args) and isinstance(call.args[0],ast.List)
                    and isinstance(call.args[0].elts[0],ast.Name) and call.args[0].elts[0].id=="bash"
                    for call in ast.walk(node)) for node in pipeline_nodes}
    assert by_owner == {"pipeline":8,"common_drift_controls":0,"prior_uri_common_drift_controls":2,"uri_common_drift_controls":1}, "Original ten Bash seams or terminal URI seam changed"
    assert len(shell_runs)==11,"Full pipeline no longer uses the supplied shell for the original ten calls and independent URI control"
    # Execute the exact current pipeline with only its eleven shell executables
    # restored to the observed old bare `bash` boundary. Git/source operations
    # remain real; the Windows WSL process result alone is a labelled stand-in.
    # The full corrected pipeline below executes the supplied real shell.
    for call in shell_runs:
        call.args[0].elts[0]=ast.Constant(value="bash")
    bare_calls=[]
    def prior_shell_boundary(args,*positional,**keywords):
        if args[0]=="bash":
            bare_calls.append(args)
            raise AssertionError("Windows Subsystem for Linux has no installed distributions.")
        return run(args,*positional,**keywords)
    scope=dict(globals(),run=prior_shell_boundary)
    exec(compile(ast.fix_missing_locations(ast.Module(body=pipeline_nodes,type_ignores=[])),
        "original-bare-bash-boundary","exec"),scope)
    legacy=out/"original-bare-bash-control"
    legacy.mkdir()
    try:
        scope["pipeline"](legacy,source,bash)
    except AssertionError as error:
        assert str(error)=="Windows Subsystem for Linux has no installed distributions."
    else:
        raise AssertionError("Original bare Bash boundary no longer propagates the actual WSL failure")
    assert len(bare_calls)==1 and bare_calls[0][0]=="bash"
    archive_inputs={}
    for mode in ("true","false"):
        raw=subprocess.check_output(["git","-C",str(ROOT),"-c","core.autocrlf="+mode,
            "-c","core.eol=lf","archive","--format=tar","HEAD","scripts/apply-branding.sh","branding/branding.env"],timeout=60)
        (out/("actual-shell-archive-autocrlf-"+mode+".tar")).write_bytes(raw)
        with tarfile.open(fileobj=io.BytesIO(raw)) as stream:
            archive_inputs[mode]={member.name:stream.extractfile(member).read()
                for member in stream if member.isfile()}
    assert archive_inputs["true"].keys()==archive_inputs["false"].keys()
    for name,data in archive_inputs["false"].items():
        original=subprocess.check_output(["git","-C",str(ROOT),"show","HEAD:"+name],timeout=60)
        assert data==original and b"\r\n" not in data
        assert archive_inputs["true"][name]==data.replace(b"\n",b"\r\n")
        assert archive_inputs["true"][name]!=data,"Original archive conversion no longer reproduced"
    (out/"exact-bash-proof.json").write_text(json.dumps({
        "actual_selected_shell":str(selected),"actual_spaced_unicode_invocation_exit":result.returncode,
        "actual_version_output_informational_only":True,
        "actual_windows_git_msys_runtime":runtime,
        "runtime_path_refusals":runtime_refusals,
        "original_version_substring_false_refusal_reproduced_mocked_version_only":True,
        "installation_layout_controls_mocked_only":True,"path_refusals":refusals,
        "existing_unix_default_preserved":True,"all_ten_pipeline_calls_exact_supplied_shell":True,
        "all_eleven_pipeline_calls_exact_supplied_shell":True,"bash_call_owners":by_owner,
        "original_nine_call_sites_and_separate_prior_owner_control_preserved":True,
        "original_bare_bash_failure_boundary_reproduced":True,
        "original_windows_wsl_process_result_mocked_only":True,
        "original_archive_autocrlf_conversion_reproduced":True,
        "corrected_private_shell_archive_equals_committed_lf_bytes":True
    },sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PASS: exact supplied Bash executes spaced Unicode script/argument/environment; seven missing/relative/WSL/non-Git paths refuse; Unix default, original nine calls, separate prior-owner and independent terminal URI shell controls preserved")


def encoding_controls(out):
    # The actual Windows runner selected cp1252 for the old implicit source
    # read. Keep that original failure as a negative control on real script
    # bytes, while accepting both legitimate checkout newline conventions.
    data=(ROOT/"scripts/apply-branding.sh").read_bytes().replace(b"\r\n",b"\n")
    expected=data.decode("utf-8")
    failures={}
    for label,newline in (("lf","\n"),("crlf","\r\n")):
        path=out/("actual-utf8-"+label+".sh")
        encoded=data.replace(b"\n",newline.encode("ascii"))
        path.write_bytes(encoded)
        try:
            path.read_text(encoding="cp1252")
        except UnicodeDecodeError as error:
            failures[label]={"encoding":error.encoding,"start":error.start,"end":error.end}
        else:
            raise AssertionError("Actual script no longer reproduces the original cp1252 refusal")
        assert path.read_text(encoding="utf-8")==expected
        path.write_text(expected,encoding="utf-8",newline=newline)
        assert path.read_bytes()==encoded,"Explicit UTF8 changed legitimate checkout bytes"
    own=Path(__file__).read_text(encoding="utf-8")
    tree=ast.parse(own)
    negative_read=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="encoding_controls")
    negative_lines={call.lineno for call in ast.walk(negative_read)
        if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=="read_text"
        and any(keyword.arg=="encoding" and isinstance(keyword.value,ast.Constant) and keyword.value.value=="cp1252"
            for keyword in call.keywords)}
    assert len(negative_lines)==1,"Expected exactly one deliberate original-encoding refusal"
    for call in ast.walk(tree):
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute):
            continue
        text_io=call.func.attr in ("read_text","write_text")
        captured_text=call.func.attr=="run" and any(
            keyword.arg=="text" and isinstance(keyword.value,ast.Constant) and keyword.value.value is True
            for keyword in call.keywords)
        if text_io or captured_text:
            encoding=next((keyword.value for keyword in call.keywords if keyword.arg=="encoding"),None)
            required="cp1252" if call.lineno in negative_lines else "utf-8"
            assert isinstance(encoding,ast.Constant) and encoding.value==required,\
                "Implicit fixture text encoding at line "+str(call.lineno)
    (out/"utf8-checkout-proof.json").write_text(json.dumps({
        "original_cp1252_refusals":failures,"actual_script_sha256":sha(data),
        "explicit_utf8_lf_crlf_bytes_preserved":True,"all_fixture_text_io_explicit":True
    },sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PASS: actual Windows cp1252 source refusal reproduced; explicit UTF8 preserves LF/CRLF Unicode bytes; all fixture text IO/captured output explicit")


def captured_stdout_controls(out):
    # Execute the exact production status print that failed in the private
    # Windows branding fixture, without invoking the product patch itself.
    source=ROOT/"scripts/patch-support-macos-window.py"
    text=source.read_text(encoding="utf-8")
    prints=[node for node in ast.walk(ast.parse(text)) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Name) and node.func.id=="print"
        and len(node.args)==1 and isinstance(node.args[0],ast.Constant)
        and isinstance(node.args[0].value,str)
        and "patched pinned Mac subwindow lifecycle" in node.args[0].value]
    assert len(prints)==1 and "→" in prints[0].args[0].value
    payload="préflight 🚀"
    code=ast.get_source_segment(text,prints[0])+"\nimport sys,os\n"+\
        "print("+repr(payload)+")\nprint(sys.stdout.encoding)\n"+\
        "print(os.environ['MIXEL_FIXTURE_STDOUT_CONTROL'])\n"
    script=out/"actual captured child préflight.py"
    script.write_text(code,encoding="utf-8",newline="\n")
    env=dict(os.environ,PYTHONIOENCODING="cp1252",MIXEL_FIXTURE_STDOUT_CONTROL=payload)
    before=dict(env)
    original=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,timeout=30)
    (out/"original-captured-cp1252-stdout.log").write_bytes(original.stdout+original.stderr)
    assert original.returncode!=0 and b"UnicodeEncodeError" in original.stderr and b"\\u2192" in original.stderr
    corrected=run([sys.executable,str(script)],out,"corrected-captured-utf8-stdout",env=env)
    expected=prints[0].args[0].value+"\n"+payload+"\nutf-8\n"+payload+"\n"
    assert corrected.stdout==expected and corrected.stderr==""
    assert env==before,"Captured subprocess encoding changed its caller's environment"
    (out/"captured-stdout-proof.json").write_text(json.dumps({
        "actual_production_print_source_sha256":sha(source.read_bytes()),
        "actual_production_print":prints[0].args[0].value,
        "original_forced_cp1252_exit":original.returncode,
        "corrected_actual_child_exit":corrected.returncode,
        "actual_unicode_stdout_exact":True,"child_declared_encoding":"utf-8",
        "caller_environment_unchanged":True,"product_patch_invoked":False,
        "actual_windows_default_requires_next_CI":os.name!="nt"
    },sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PASS: exact production Unicode status print fails in actual cp1252 child; same captured subprocess runs with UTF8 stdout, exact Unicode and unchanged caller environment")


def assert_pipeline_refusal(result,marker):
    assert result.returncode!=0 and marker in result.stderr, (
        "Expected exact fatal marker: "+marker+"; actual exit="+str(result.returncode)+
        "; bounded child stderr:\n"+result.stderr[:4096])


def pipeline_failure_diagnostics_controls(out):
    wrong=run([sys.executable,"-c","import sys; sys.stderr.write('wrong native marker\\n'+'x'*5000+'PRIVATE_TAIL'); sys.exit(19)"],out,"wrong-pipeline-marker-control",success=False)
    try:
        assert_pipeline_refusal(wrong,"expected exact marker")
    except AssertionError as error:
        message=str(error)
        assert "actual exit=19" in message and "wrong native marker" in message
        assert "PRIVATE_TAIL" not in message and len(message)<4300
    else:
        raise AssertionError("Different fatal marker was accepted")
    successful=run([sys.executable,"-c","import sys; sys.stderr.write('expected exact marker')"],out,"successful-pipeline-marker-control")
    try:
        assert_pipeline_refusal(successful,"expected exact marker")
    except AssertionError as error:
        assert "actual exit=0" in str(error)
    else:
        raise AssertionError("Successful child was accepted as a fatal refusal")
    fatal=run([sys.executable,"-c","import sys; sys.stderr.write('expected exact marker'); sys.exit(23)"],out,"fatal-pipeline-marker-control",success=False)
    assert_pipeline_refusal(fatal,"expected exact marker")
    print("PASS: exact fatal markers and nonzero child exits remain mandatory; mismatches retain bounded actual stderr/exit")


def dependency_profile(out,dependency,setting,label):
    # A Git newline policy belongs to the complete checkout. Rewriting just
    # keyboard.rs leaves other CRLF tracked files dirty under autocrlf=false.
    tracked=subprocess.check_output(["git","-C",str(dependency),"ls-files","-z"],timeout=60).decode("utf-8").split("\0")[:-1]
    saved={name:(dependency/name).read_bytes() for name in tracked}
    originals={name:subprocess.check_output(["git","-C",str(dependency),"cat-file","blob","HEAD:"+name],timeout=60) for name in tracked}
    modified={name:data for name,data in saved.items() if data.replace(b"\r\n",b"\n")!=originals[name]}
    assert set(modified)<={"src/linux/grab.rs","src/keycodes/android.rs",*R.FILES},"Unexpected fixture modification before profile conversion"
    if "src/keycodes/android.rs" in modified:
        source=originals["src/keycodes/android.rs"]
        assert sha(source)=="461b84c7676baf5cc3356bfba9ead2c8ce32a52d8a403843978b1c626ded9c92"
        assert modified["src/keycodes/android.rs"].replace(b"\r\n",b"\n")==source.replace(b"    BackQuote, 75,\n",b"    BackQuote, 68,\n",1)
    run(["git","-C",str(dependency),"config","--local","core.autocrlf",setting],out,label+"-config")
    run(["git","-C",str(dependency),"config","--local","core.eol","crlf" if setting=="true" else "lf"],out,label+"-eol")
    for name in tracked:
        target=dependency/name
        assert target.is_file() and not target.is_symlink() and target.stat().st_nlink==1
        target.unlink()
    run(["git","-C",str(dependency),"-c","core.autocrlf="+setting,"-c","core.eol="+("crlf" if setting=="true" else "lf"),"checkout","HEAD","--","."],out,label+"-checkout-all")
    newline=b"\r\n" if setting=="true" else b"\n"
    for name in tracked:
        actual=(dependency/name).read_bytes()
        assert actual==originals[name].replace(b"\n",newline),name
    # Preserve verified overlay content with the selected Mac checkout
    # newlines. The Linux producer uses native text IO; retain its exact bytes
    # so the complete macro remains byte-idempotent on either host.
    for name,data in modified.items():
        (dependency/name).write_bytes(data if name=="src/linux/grab.rs" else data.replace(b"\r\n",b"\n").replace(b"\n",newline))
    (out/(label+"-tracked-profile.json")).write_text(json.dumps({
        "autocrlf":setting,"tracked_count":len(tracked),
        "all_pristine_paths_materialized_through_git":True,
        "retained_verified_overlay_paths":sorted(modified),
        "verified_overlay_logical_sha256":{name:sha(data.replace(b"\r\n",b"\n")) for name,data in modified.items()},
        "git_attributes":subprocess.check_output(["git","-C",str(dependency),"check-attr","-a","--",*tracked],timeout=60).decode("utf-8"),
        "tracked_sha256":{name:sha((dependency/name).read_bytes()) for name in tracked}
    },sort_keys=True,indent=2)+"\n",encoding="utf-8")


def without_uri_owner_commands(body):
    """Remove only the three literal terminal URI commands in historical macros."""
    lines = body.splitlines(keepends=True)
    prefix = 'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-uri-routing.py" --root "$RDREPO" '
    expected = [prefix + flags + '\n' for flags in (
        '--restore --check --before-branding', '--restore', '--after-branding')]
    actual = [line for line in lines if '/patch-support-uri-routing.py"' in line]
    assert actual == expected and all(lines.count(line) == 1 for line in expected), "Exact terminal URI owner commands changed"
    return ''.join(line for line in lines if line not in actual)


def uri_common_drift_controls(out, repo, copied, bash, script, env, label):
    """Require the actual outer URI whole-file refusal and conserve every byte."""
    before_all = snapshot(repo)
    target = repo / 'flutter/lib/common.dart'
    saved = target.read_bytes()
    try:
        target.write_bytes(saved + b'// unapproved URI owner drift\n')
        poisoned = snapshot(repo)
        failed = run([bash, script.as_posix()], out, 'pipeline-' + label + '-uri-refusal',
                     cwd=copied, env=env, success=False,full_branding=True)
        assert_pipeline_refusal(failed, 'Changed or partial URI routing profile')
        assert snapshot(repo) == poisoned, 'Terminal URI refusal changed source'
    finally:
        target.write_bytes(saved)
        assert snapshot(repo) == before_all, 'Terminal URI drift control changed supplied source'


def common_drift_controls(out, repo, copied, bash, script, env, label):
    before = snapshot(repo)
    with input_lifecycle.current_source(repo) as previous_input:
        prior_macro = copied / 'scripts' / ('prior-input-refusal-' + label + '.sh')
        prior_macro.write_bytes(input_lifecycle.project_to_canonical(script.read_bytes(), 'scripts/apply-branding.sh'))
        common_drift_controls_previous(out, previous_input, copied, bash, prior_macro,
                                       dict(env, RDREPO=previous_input.as_posix()), label)
    assert snapshot(repo) == before, 'Input lifecycle projection changed supplied source'


def common_drift_controls_previous(out, repo, copied, bash, script, env, label):
    before = snapshot(repo)
    uri_common_drift_controls(out, repo, copied, bash, script, env, label)
    uri_spec = importlib.util.spec_from_file_location('mixel_drift_terminal_uri', ROOT / 'scripts/patch-support-uri-routing.py')
    uri = importlib.util.module_from_spec(uri_spec)
    uri_spec.loader.exec_module(uri)
    # Preserve both prior Linux and startup refusals on their exact historical
    # source. Only the three strictly counted URI macro commands are removed.
    with uri.previous(repo) as previous:
        historical = copied / 'scripts' / ('prior-uri-refusal-' + label + '.sh')
        historical.write_text(without_uri_owner_commands(script.read_text(encoding='utf-8')),
                              encoding='utf-8', newline='')
        prior_uri_common_drift_controls(out, previous, copied, bash, historical,
                                       dict(env, RDREPO=previous.as_posix()), label)
    assert snapshot(repo) == before, 'URI projection changed supplied source'


def prior_uri_common_drift_controls(out, repo, copied, bash, script, env, label):
    supplied_before = snapshot(repo)
    try:
        name = "flutter/lib/common.dart"
        target = repo / name
        saved = target.read_bytes()
        try:
            target.write_bytes(saved + b"// unapproved drift\n")
            before = snapshot(repo)
            failed = run([bash,script.as_posix()], out, "pipeline-"+label+"-refusal", cwd=copied, env=env, success=False,full_branding=True)
            # The final Linux owner now verifies this entire file before earlier owners.
            assert_pipeline_refusal(failed, "Pinned or partial Linux close profile changed")
            assert snapshot(repo) == before, "Drift refusal changed another pipeline file"
        finally:
            target.write_bytes(saved)

        native_spec = importlib.util.spec_from_file_location("mixel_drift_native_close", ROOT / "scripts/patch-support-linux-child-window-close.py")
        native = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native)
        # Separately retain the original Mac startup refusal behind exact inverses.
        with native.previous(repo) as without_native:
            linux = native.prior_namespace()
            with linux["previous"](without_native) as previous:
                body = script.read_text(encoding="utf-8")
                lines = body.splitlines(keepends=True)
                removed = [line for line in lines if '/patch-support-linux-window-close.py"' in line or '/patch-support-linux-child-window-close.py"' in line]
                prefix = 'python3 "$(dirname "${BASH_SOURCE[0]}")/'
                expected = [prefix + helper + '" --root "$RDREPO" ' + flags + '\n'
                            for helper, flags in (
                                ("patch-support-linux-child-window-close.py", "--restore --check --before-branding"),
                                ("patch-support-linux-child-window-close.py", "--restore"),
                                ("patch-support-linux-window-close.py", "--restore --check --before-branding"),
                                ("patch-support-linux-window-close.py", "--restore"),
                                ("patch-support-linux-window-close.py", "--after-branding"),
                                ("patch-support-linux-child-window-close.py", "--after-branding"))]
                assert removed == expected and all(lines.count(line) == 1 for line in expected), "Exact final owner command inventory changed"
                legacy_script = copied / "scripts" / ("prior-startup-refusal-"+label+".sh")
                legacy_script.write_text("".join(line for line in lines if line not in removed), encoding="utf-8", newline="")
                prior_target = previous / name
                prior_saved = prior_target.read_bytes()
                try:
                    prior_target.write_bytes(prior_saved + b"// unapproved drift\n")
                    before = snapshot(previous)
                    failed = run([bash,legacy_script.as_posix()], out, "pipeline-"+label+"-prior-startup-refusal", cwd=copied,
                                 env=dict(env, RDREPO=previous.as_posix()), success=False,full_branding=True)
                    assert_pipeline_refusal(failed, "Pinned Mac startup input digest mismatch; refusing all writes")
                    assert snapshot(previous) == before, "Prior startup refusal changed another pipeline file"
                finally:
                    prior_target.write_bytes(prior_saved)
    finally:
        assert snapshot(repo) == supplied_before, "Drift controls changed supplied source bytes"


def pipeline(out,source,bash):
    copied=out/"pipeline-source"
    copied.mkdir()
    archive=subprocess.check_output(["git","-C",str(ROOT),"-c","core.autocrlf=false","-c","core.eol=lf","archive","--format=tar","HEAD","scripts","branding"],timeout=60)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile():
                path=copied/member.name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(stream.extractfile(member).read())
    # Include the exact current root integration and the reviewed new helpers,
    # without mutating root-owned files.
    for name in ("apply-branding.sh","patch-support-url-log-privacy.py","test-support-url-log-privacy.py","patch-support-macos-cleanup.py",
                 "patch-macos-service-label.py","patch-macos-update.py",
                 "patch-support-invite.py","patch-secure-support.py",
                 "patch-support-network.py","patch-support-linux.py",
                 "patch-support-signals.py","patch-support-clipboard.py",
                 "patch-support-windows-clipboard.py","patch-support-video.py",
                 "patch-support-macos.py","patch-support-wakelock.py",
                 "patch-support-macos-uri.py","patch-support-macos-url-ack.py",
                 "patch-support-flutter-analysis.py","test-support-flutter-analysis.py",
                 "patch-support-macos-termination.py","test-support-macos-termination.py",
                 "patch-support-macos-texture.py",
                 "patch-support-macos-window.py",
                 "patch-flutter-branding.py",
                 "test-flutter-branding.py","verify-flutter-branding.py",
                 "patch-support-flutter-sdk-compatibility.py","test-support-flutter-sdk-compatibility.py",
                 "test-support-macos-cleanup.py","test-support-macos-window.py",
                 "patch-support-macos-rdev.py","patch-support-input.py",
                 "patch-support-file-transfer-window.py","test-support-file-transfer-window.py",
                 "patch-support-file-dialog-lifecycle.py","test-support-file-dialog-lifecycle.py",
                 "patch-support-input-lifecycle.py","support-input-lifecycle-engine.py","support-input-lifecycle-plan.json",
                 "input_lifecycle_integration_projection.py",
                 "receiver_input_integration_projection.py","receiver-input-integration-plan.json",
                 "patch-support-receiver-input.py","support-receiver-input-plan.json",
                 "support-receiver-input.rs","support-receiver-input-windows.rs",
                 "patch-support-receiver-native.py","support-receiver-native-plan.json",
                 "support-receiver-native.rs","support-receiver-receipts.rs",
                 "patch-support-keyboard-default.py",
                 "patch-support-uinput-scroll.py",
                 "patch-support-uinput-device.py","test-support-uinput-device.py",
                 "patch-support-native-warnings.py","test-support-native-warnings.py",
                 "patch-support-compiler-cleanup.py","test-support-compiler-cleanup.py",
                 "patch-support-uri-routing.py","test-support-uri-routing.py",
                 "patch-support-macos-gui-launch.py","test-support-macos-gui-launch.py","support-macos-gui-launch.rs",
                 "patch-support-linux-window-close.py","test-support-linux-window-close.py",
                 "patch-support-linux-child-window-close.py","test-support-linux-child-window-close.py",
                 "patch-support-fingerprint.py","test-support-fingerprint.py",
                 "patch-support-fatal-crash.py","test-support-fatal-crash.py",
                 "patch-macos-startup.py","test-macos-startup.py","test-macos-startup-ui.dart","macos-startup-failure.dart",
                 "patch-support-macos-tray-url.py","test-support-macos-tray-url.py",
                 "patch-macos-update.py","test-macos-update.py","macos-update.scpt","macos-update-bundle.sh"):
        shutil.copyfile(ROOT/"scripts"/name,copied/"scripts"/name)
    # These helpers consume checked-in source resources and the complete
    # vendored URI plugin. HEAD may predate reviewed incoming files, so retain
    # the actual current resource bytes alongside the exact helper inventory.
    for name in ("support-network.rs","support-invite-reporter.dart",
                 "support-invite-guard.rs","support-macos-url-queue.rs",
                 "macos-update.scpt","macos-update-bundle.sh",
                 "fixtures/flutter-icon-upstream.svg"):
        target=copied/"scripts"/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/"scripts"/name,target)
    shutil.copytree(ROOT/"scripts/vendor/uni_links_desktop",
                    copied/"scripts/vendor/uni_links_desktop",dirs_exist_ok=True)
    for name in ("branding.env","custom.txt","icon-16.png","icon-32.png",
                 "icon-48.png","icon-64.png","icon-128.png","icon-256.png",
                 "icon-512.png","icon-1024.png"):
        shutil.copyfile(ROOT/"branding"/name,copied/"branding"/name)
    copy_current_pipeline_inputs(copied)
    run([sys.executable,str(copied/"scripts/test-flutter-branding.py")],
        out,"pipeline-current-flutter-branding-controls")
    script=copied/"scripts/apply-branding.sh"
    shell_source="$"+"{BASH_SOURCE[0]}"
    hook='python3 "$(dirname "'+shell_source+'")/patch-support-flutter-analysis.py"\n'
    text=script.read_text(encoding="utf-8")
    if hook not in text:
        text+="\n"+hook
    else:
        assert text.count(hook)==1
    # This private shell copy must keep committed LF semantics independently
    # of the host Git checkout/text writer defaults. Upstream test inputs still
    # exercise both actual LF and CRLF throughout the complete branding matrix.
    script.write_bytes(text.encode("utf-8"))
    originals={name:pinned(source,name) for name in P.FILES if name!="flutter/lib/common.dart"}
    originals[P.CLIENT]=pinned(source,P.CLIENT)
    assert sha(originals[P.CLIENT])==P.CLIENT_SHA256
    for app_newlines,initial_profile in (("lf","false"),("lf","true"),("crlf","false"),("crlf","true")):
        newline=b"\n" if app_newlines=="lf" else b"\r\n"
        label=app_newlines+"-initial-"+initial_profile
        repo=out/("pipeline-lf" if label=="lf-initial-false" else "pipeline-"+label)
        run(["git","clone","--quiet","--shared",str(source),str(repo)],out,"clone-"+label)
        run(["git","-C",str(repo),"checkout","--quiet",P.PIN],out,"checkout-"+label)
        library=source/"libs/hbb_common"
        library_source=str(library) if (library/".git").exists() else "https://github.com/rustdesk/hbb_common"
        run(["git","clone","--quiet",library_source,str(repo/"libs/hbb_common")],out,"hbb-clone-"+label)
        library_pin=subprocess.check_output(["git","-C",str(repo),"ls-tree","HEAD","libs/hbb_common"]).decode().split()[2]
        run(["git","-C",str(repo/"libs/hbb_common"),"checkout","--quiet",library_pin],out,"hbb-checkout-"+label)
        for name in P.FILES:
            (repo/name).write_bytes(pinned(source,name).replace(b"\n",newline))
        # Reproduce the complete Windows-inherited checkout before the first
        # macro, not just one CRLF file introduced into a Unix-LF dependency.
        dependency=repo/R.DEPENDENCY
        dependency.mkdir(parents=True)
        for arguments in (["init","--quiet"],
                          ["config","--local","core.autocrlf",initial_profile],
                          ["config","--local","core.eol","crlf" if initial_profile=="true" else "lf"],
                          ["remote","add","origin",R.UPSTREAM],
                          ["fetch","--quiet","--depth","1","origin",R.PIN],
                          ["checkout","--quiet","--detach",R.PIN]):
            run(["git","-C",str(dependency),"-c","core.autocrlf="+initial_profile,"-c","core.eol="+("crlf" if initial_profile=="true" else "lf"),*arguments],out,"pipeline-"+label+"-initial-"+arguments[0]+("-eol" if "core.eol" in arguments else ""))
        initial_tracked=snapshot(dependency)
        initial_newline=b"\r\n" if initial_profile=="true" else b"\n"
        for relative in initial_tracked:
            logical=subprocess.check_output(["git","-C",str(dependency),"cat-file","blob","HEAD:"+relative],timeout=60)
            assert (dependency/relative).read_bytes()==logical.replace(b"\n",initial_newline),relative
        (out/("pipeline-"+label+"-initial-tracked.json")).write_text(json.dumps(initial_tracked,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        env=dict(os.environ,RDREPO=repo.as_posix(),BRANDING=(copied/"branding").as_posix(),GIT_CONFIG_COUNT="0")
        run([sys.executable,str(copied/"scripts/patch-support-input.py")],out,"pipeline-"+label+"-initial-input",env=env)
        run([bash,script.as_posix()],out,"pipeline-"+label+"-1",cwd=copied,env=env,full_branding=True)
        with startup_baseline(repo) as checked_repo:
            F.verify_previous_layers(checked_repo) if (checked_repo/F.GENERATED).exists() else S.apply(checked_repo,check=True)
            for name,(_,corrected,*_) in P.FILES.items():
                actual=(checked_repo/name).read_bytes().replace(b"\r\n",b"\n")
                if name in F.COMPOSED and (checked_repo/F.GENERATED).exists(): actual=F.transform(name,actual,restore=True)
                canonical=(T.regular(checked_repo/"flutter/.mixel-dependencies/macos_application_exit",name)
                           if name in T.FILES else actual)
                assert sha(canonical)==corrected,name
                assert actual==(T.transform(name,canonical) if name in T.FILES else canonical),name
        first=snapshot(repo)
        run([bash,script.as_posix()],out,"pipeline-"+label+"-2",cwd=copied,env=env,full_branding=True)
        assert snapshot(repo)==first,"Full pipeline second pass changed bytes"
        (out/("pipeline-"+label+"-snapshot.json")).write_text(json.dumps(first,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        android_target=dependency/"src/keycodes/android.rs"
        android_saved=android_target.read_bytes()
        android_newline=b"\r\n" if b"\r\n" in android_saved else b"\n"
        android_old=subprocess.check_output(["git","-C",str(dependency),"cat-file","blob","HEAD:src/keycodes/android.rs"],timeout=60)
        assert sha(android_old)=="461b84c7676baf5cc3356bfba9ead2c8ce32a52d8a403843978b1c626ded9c92"
        assert android_saved.replace(b"\r\n",b"\n")==android_old.replace(b"    BackQuote, 75,\n",b"    BackQuote, 68,\n",1)
        # A previously branded exact old table may pass only the read-only
        # restore prefix; the input producer must fix it before Mac reapply.
        android_target.write_bytes(android_old.replace(b"\n",android_newline))
        run([bash,script.as_posix()],out,"pipeline-"+label+"-android-legacy-migration",cwd=copied,env=env,full_branding=True)
        assert snapshot(repo)==first,"Exact old Android migration changed another final pipeline byte"
        android_target.write_bytes(android_saved.replace(b"    Quote, 75,",b"    Quote, 76,"))
        android_before=snapshot(repo)
        android_failed=run([bash,script.as_posix()],out,"pipeline-"+label+"-android-drift-refusal",cwd=copied,env=env,success=False,full_branding=True)
        assert_pipeline_refusal(android_failed,"Pinned Android input overlay is required")
        assert snapshot(repo)==android_before,"Android table drift restored an earlier valid overlay before refusal"
        android_target.write_bytes(android_saved)
        # A late dependency refusal used to restore valid SDK/Quit overlays
        # first. Exercise the entire actual shell macro on the fully composed
        # state, so helper-local zero-write checks cannot hide that regression.
        dependency_target=repo/".mixel-deps/rdev/src/macos/keyboard.rs"
        dependency_saved=dependency_target.read_bytes()
        tracked_saved={name:(dependency/name).read_bytes() for name in subprocess.check_output(["git","-C",str(dependency),"ls-files","-z"],timeout=60).decode("utf-8").split("\0")[:-1]}
        dependency_config=repo/".mixel-deps/rdev/.git/config"
        dependency_config_saved=dependency_config.read_bytes()
        try:
            for checkout_crlf,newline in (("false",b"\n"),("true",b"\r\n")):
                dependency_profile(out,dependency,checkout_crlf,"pipeline-"+label+"-rdev-"+checkout_crlf)
                inherited=dependency_target.read_bytes()
                assert inherited==dependency_saved.replace(b"\r\n",b"\n").replace(b"\n",newline)
                clean=snapshot(repo)
                run([bash,script.as_posix()],out,"pipeline-"+label+"-rdev-clean-"+checkout_crlf,cwd=copied,env=env,full_branding=True)
                assert snapshot(repo)==clean,"Clean inherited dependency checkout changed bytes"
                if checkout_crlf=="true":
                    # Reproduce the old fixture failure: the source helper
                    # correctly refuses CRLF input with an LF-only suffix.
                    dependency_target.write_bytes(inherited+b"// unapproved dependency drift\n")
                    original_before=snapshot(repo)
                    original_failed=run([bash,script.as_posix()],out,"pipeline-"+label+"-rdev-original-mixed-refusal",cwd=copied,env=env,success=False,full_branding=True)
                    assert_pipeline_refusal(original_failed,"Mixed or unsupported dependency newlines")
                    assert "Unrelated rdev source drift" not in original_failed.stderr
                    assert snapshot(repo)==original_before,"Original mixed-newline refusal changed another file"
                dependency_target.write_bytes(inherited+b"// unapproved dependency drift"+newline)
                dependency_before=snapshot(repo)
                dependency_failed=run([bash,script.as_posix()],out,"pipeline-"+label+"-rdev-refusal-"+checkout_crlf,cwd=copied,env=env,success=False,full_branding=True)
                assert_pipeline_refusal(dependency_failed,"Unrelated rdev source drift")
                assert snapshot(repo)==dependency_before,"Dependency refusal changed an earlier overlay file"
                dependency_target.write_bytes(inherited)
        finally:
            for relative,data in tracked_saved.items():
                (dependency/relative).write_bytes(data)
            dependency_config.write_bytes(dependency_config_saved)
        assert dependency_config.read_bytes()==dependency_config_saved,"Dependency profile changed the original Git configuration"
        assert snapshot(repo)==first,"Dependency profile did not restore complete original checkout bytes"
        (out/("pipeline-"+label+"-restoration.json")).write_text(json.dumps({
            "complete_checkout_bytes_restored":True,"original_git_config_bytes_restored":True,
            "git_config_sha256_before":sha(dependency_config_saved),
            "git_config_sha256_after":sha(dependency_config.read_bytes()),
            "tracked_count":len(tracked_saved)
        },sort_keys=True,indent=2)+"\n",encoding="utf-8")
        dependency=repo/".mixel-deps/rdev"
        missing_dependency=out/("retained-missing-rdev-"+label)
        dependency.rename(missing_dependency)
        try:
            missing_before=snapshot(repo)
            missing_failed=run([bash,script.as_posix()],out,"pipeline-"+label+"-rdev-missing-refusal",cwd=copied,env=env,success=False,full_branding=True)
            assert_pipeline_refusal(missing_failed,"Missing owned rdev dependency checkout")
            assert snapshot(repo)==missing_before,"Missing dependency refusal changed an earlier overlay file"
        finally:
            missing_dependency.rename(dependency)
        name="flutter/lib/common.dart"
        target=repo/name
        saved=target.read_bytes()
        common_drift_controls(out, repo, copied, bash, script, env, label)
        if app_newlines=="lf":
            canonical=T.regular(repo/"flutter/.mixel-dependencies/macos_application_exit",name)
            common=canonical
            assert sha(common)==P.FILES[name][0]
            originals[name]=common
    print("PASS: exact current complete branding, cleanup, analyzer and final Quit composition; app LF/CRLF crossed with complete initial LF/CRLF dependency checkouts, each twice byte-identical; whole tracked profiles and Dart/dependency drift fail without mutation")
    return originals


def fixture(root,files,newline=b"\n"):
    root.mkdir()
    (root/"Cargo.toml").write_bytes(b'[package]\nversion = "1.4.6"\n'.replace(b"\n",newline))
    for name,data in files.items():
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data.replace(b"\n",newline))


def refusal(root,out):
    before=snapshot(root)
    try:
        P.apply(root)
    except (RuntimeError,OSError,ValueError):
        assert snapshot(root)==before
        return
    raise AssertionError("Unqualified analyzer input was accepted")


def source_controls(out,files):
    common="flutter/lib/common.dart"
    assert sha(files[common])==P.FILES[common][0]
    # Build the historical no-ACK fixture only by an exact pinned reversal.
    begin=b"// Mixel Mac support forwarding readiness. Local runtime state, never saved preferences.\n"
    end=b"// Mixel support invite handoff: presence only; customer accepts every connection.\n"
    assert files[common].count(begin)==files[common].count(end)==1
    historical=dict(files)
    historical[common]=files[common][:files[common].index(begin)]+files[common][files[common].index(end):]
    assert sha(historical[common])==P.HISTORICAL_COMMON[0]
    for profile,inputs in (("current-ack",files),("historical-no-ack",historical)):
        expected=set(P.FILES)-({common} if profile=="current-ack" else set())
        assert len(expected)==(5 if profile=="current-ack" else 6)
        for label,newline in (("lf",b"\n"),("crlf",b"\r\n")):
            root=out/(("source-" if profile=="current-ack" else "historical-source-")+label)
            fixture(root,inputs,newline)
            before=snapshot(root)
            P.apply(root)
            after=snapshot(root)
            assert {name for name in before if before[name]!=after[name]}==expected
            for name in P.FILES:
                assert (root/name).read_bytes()==P.transform(name,inputs[name]).replace(b"\n",newline)
            assert (b"import 'dart:math';\n".replace(b"\n",newline) in (root/common).read_bytes())==(profile=="current-ack")
            P.apply(root)
            assert snapshot(root)==after
    count=0
    for name in list(P.FILES)+[P.CLIENT,"Cargo.toml"]:
        root=out/("refuse-"+name.replace("/","-").replace(".","-"))
        shutil.copytree(out/"source-lf",root)
        target=root/name
        target.write_bytes(target.read_bytes()+b"// drift\n")
        if name=="Cargo.toml":
            target.write_text('[package]\nversion = "1.4.5"\n',encoding="utf-8")
        refusal(root,out);count+=1
    for label,operation in (
        ("symlink",lambda path: (path.unlink(),path.symlink_to(out/"source-lf"/P.CLIENT))),
        ("hardlink",lambda path: (path.unlink(),os.link(out/"source-lf"/P.CLIENT,path))),
        ("mixed-newlines",lambda path:path.write_bytes(files[P.CLIENT].replace(b"\n",b"\r\n",1))),
    ):
        root=out/("refuse-"+label)
        shutil.copytree(out/"source-lf",root)
        operation(root/P.CLIENT)
        refusal(root,out);count+=1
    for label,data in (
        ("missing-package",b'version = "1.4.6"\n'),
        ("wrong-package",b'[other]\nversion = "1.4.6"\n'),
        ("duplicate-package",b'[package]\nversion = "1.4.6"\n[package]\nversion = "1.4.6"\n'),
        ("duplicate-version",b'[package]\nversion = "1.4.6"\nversion = "1.4.6"\n'),
        ("dependency-only-version",b'[package]\nversion = "1.4.5"\n[dependencies]\nversion = "1.4.6"\n'),
    ):
        root=out/("refuse-"+label)
        shutil.copytree(out/"source-lf",root)
        (root/"Cargo.toml").write_bytes(data)
        refusal(root,out);count+=1
    print("PASS: current ACK changes exactly five targets and retains used dart:math; historical no-ACK changes six; LF/CRLF byte idempotence and "+str(count)+" fatal source/constructor/link/newline controls")


def function(source,anchor):
    start=source.index(anchor)
    first=source.index("{",start+len(anchor)-1)
    end,depth=first+1,1
    while depth:
        depth+=(source[end]=="{")-(source[end]=="}")
        end+=1
    return source[start:end]


def dart_controls(out,files,dart):
    client=files[P.CLIENT].decode()
    client=client[client.index("class Client {"):client.index("  Client.fromJson(")]+"}\n"
    prefix="""class RxInt { RxInt(this.value); final int value; }
extension ReactiveInt on int { RxInt get obs=>RxInt(this); }
"""
    for label,data in (("original",files["flutter/test/cm_test.dart"]),
                       ("corrected",P.transform("flutter/test/cm_test.dart",files["flutter/test/cm_test.dart"]))):
        source=data.decode()
        clients=source[source.index("final testClients"):source.index("\n/// flutter run")]
        main="""void main() {
  if(testClients.length!=4) throw StateError('Missing CM fixture clients');
  for(var i=0;i<testClients.length;i++) {
    final value=testClients[i];
    if(value.id!=i || value.authorized || value.isFileTransfer ||
        value.isViewCamera || value.isTerminal || value.file ||
        !value.keyboard || value.clipboard || value.audio ||
        value.name.isEmpty || value.peerId.isEmpty) {
      throw StateError('Nine-field constructor changed CM fixture identity/permission defaults');
    }
  }
  print('PASS: actual pinned Client nine-field constructor and four corrected CM clients preserve identity and false file/terminal defaults');
}
"""
        path=out/(label+"-client-control.dart")
        path.write_text(prefix+client+clients+main,encoding="utf-8")
        result=run([str(dart),"analyze","--format=machine",str(path)],out,label+"-client-analyze",success=label=="corrected")
        if label=="original":
            errors=[line for line in result.stdout.splitlines() if line.startswith("ERROR|")]
            assert result.returncode!=0 and len(errors)==4 and all("|EXTRA_POSITIONAL_ARGUMENTS|" in line for line in errors)
        else:
            run([str(dart),str(path)],out,"actual-client-constructor")
    terminal="flutter/lib/mobile/pages/terminal_page.dart"
    original=function(files[terminal].decode(),"  void _sendKeyToTerminal(String key) {")
    corrected=function(P.transform(terminal,files[terminal]).decode(),"  void _sendKeyToTerminal(String key) {")
    program="""class TerminalModel {
  final keys=<String>[];
  void sendVirtualKey(String key) {keys.add(key);}
}
"""+ "\n".join("class "+name+" { final _terminalModel=TerminalModel();\n"+method+"\n}" for name,method in (
        ("Original",original),("Corrected",corrected)))+r"""
void main() {
  final original=Original(),corrected=Corrected();
  for(final key in ['Esc','Tab','Ctrl+C','↑','↓','→','←','Home','End','PgUp','PgDn','','a','é','🚀']) {
    original._sendKeyToTerminal(key);corrected._sendKeyToTerminal(key);
  }
  if(original._terminalModel.keys.join('|')!=corrected._terminalModel.keys.join('|')||
      corrected._terminalModel.keys.length!=15) {
    throw StateError('Removing redundant nullable check changed actual terminal key mapping');
  }
  print('PASS: actual original/corrected terminal methods emit identical control, navigation, empty, Unicode and ordinary keys');
}
"""
    path=out/"actual-terminal-key-control.dart"
    path.write_text(program,encoding="utf-8")
    run([str(dart),str(path)],out,"actual-terminal-key-control")
    print("PASS: actual original Client fixture fails with exactly four argument errors; corrected actual constructor compiles/runs; all actual terminal key mappings conserved")


def diagnostic_counts(text):
    parts=[line.split(" • ") for line in text.splitlines() if " • " in line]
    return collections.Counter(part[0].strip() for part in parts),parts


def analyze_app(out,source,flutter,files,package_config_source=None,_input_checked=False):
    # Ownership projections omit generated caches. Retain the supplied app's
    # exact resolved package configuration as a separate read-only fixture input.
    if package_config_source is None:
        package_config_source=source/"flutter/.dart_tool/package_config.json"
    with startup_baseline(source, _input_checked=_input_checked) as plain:
        if plain != source.resolve():
            return analyze_app(out,plain,flutter,files,package_config_source,_input_checked=True)
    # Validate every final ownership layer without changing the supplied app.
    source_before=snapshot(source/"flutter")
    sdk_overlay=(source/S.OWNED).exists() or (source/S.OWNED).is_symlink()
    application_exit=(source/T.GENERATED).exists() or (source/T.GENERATED).is_symlink()
    file_transfer=(source/F.GENERATED).exists() or (source/F.GENERATED).is_symlink()
    if file_transfer: F.apply(source,restore=True,check=True); F.verify_previous_layers(source)
    elif sdk_overlay: S.apply(source,check=True)
    elif application_exit: T.apply(source,check=True)
    for name in P.FILES:
        canonical=(T.regular(source/"flutter/.mixel-dependencies/macos_application_exit",name)
                   if application_exit and name in T.FILES else (source/name).read_bytes())
        P.transform(name,canonical)
    checkout=out/"complete-app-fixture"
    for name in ("lib","test","assets",".mixel-dependencies","local_plugins"):
        if (source/"flutter"/name).exists():
            shutil.copytree(source/"flutter"/name,checkout/"flutter"/name)
    for name in ("pubspec.yaml","pubspec.lock","analysis_options.yaml",".dart_tool/package_config.json"):
        target=checkout/"flutter"/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(package_config_source if name==".dart_tool/package_config.json" else source/"flutter"/name,target)
    shutil.copyfile(source/"Cargo.toml",checkout/"Cargo.toml")
    if application_exit:
        for name in T.FILES:
            target=checkout/name; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,target)
    if file_transfer: F.apply(checkout,restore=True)
    if sdk_overlay: S.apply(checkout,restore=True)
    if application_exit: T.apply(checkout,restore=True)
    for name in P.FILES: (checkout/name).write_bytes(files[name])
    env=dict(os.environ,CI="true",FLUTTER_SUPPRESS_ANALYTICS="true",DART_SUPPRESS_ANALYTICS="true")
    version_run=run([str(flutter),"--version","--machine"],out,"actual-full-app-sdk-version",env=env)
    version=json.loads(version_run.stdout)["frameworkVersion"]
    assert version in ("3.24.5",S.SDK),"Unqualified Flutter analyzer SDK"
    new_warnings=7 if version==S.SDK else 0
    historical=sha(files["flutter/lib/common.dart"])==P.HISTORICAL_COMMON[0]
    base_warnings=5 if historical else 4
    before=snapshot(checkout/"flutter")
    baseline=run([str(flutter),"analyze","--no-pub","--no-fatal-infos"],out,"actual-full-app-baseline",cwd=checkout/"flutter",env=env,success=False)
    counts,_=diagnostic_counts(baseline.stdout+baseline.stderr)
    assert counts["error"]==4 and counts["warning"]==base_warnings+new_warnings,counts
    for name in P.FILES:
        path=out/"actual-original"/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((checkout/name).read_bytes())
    P.apply(checkout)
    middle_run=run([str(flutter),"analyze","--no-pub","--no-fatal-infos"],out,"actual-full-app-original-analysis-corrected",cwd=checkout/"flutter",env=env,success=new_warnings==0)
    middle,_=diagnostic_counts(middle_run.stdout+middle_run.stderr)
    assert middle["error"]==0 and middle["warning"]==new_warnings and middle["info"]==counts["info"],middle
    middle_source=snapshot(checkout/"flutter")
    expected_changes=set(P.FILES)-({"flutter/lib/common.dart"} if not historical else set())
    assert len(expected_changes)==(6 if historical else 5)
    assert {name for name in before if before[name]!=middle_source[name]}=={name.removeprefix("flutter/") for name in expected_changes}
    for name in P.FILES:
        path=out/"actual-corrected"/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((checkout/name).read_bytes())
    # Reapply exact resource/Quit and SDK overlays only after proving that the
    # original profile-specific correction retained its independent byte boundary.
    if application_exit: T.apply(checkout)
    if version==S.SDK or sdk_overlay: S.apply(checkout)
    if file_transfer: F.apply(checkout)
    corrected=run([str(flutter),"analyze","--no-pub","--no-fatal-infos"],out,"actual-full-app-corrected",cwd=checkout/"flutter",env=env)
    fixed,_=diagnostic_counts(corrected.stdout+corrected.stderr)
    assert fixed["error"]==0 and fixed["warning"]==0 and fixed["info"]==counts["info"],fixed
    assert snapshot(source/"flutter")==source_before,"Supplied analysis checkout changed"
    (out/"complete-app-analysis-proof.json").write_text(json.dumps(
        {"flutter_version":version,"baseline_exit":baseline.returncode,"corrected_exit":corrected.returncode,
         "baseline":dict(counts),"original_analysis_layer_corrected":dict(middle),"corrected":dict(fixed),
         "exact_six_target_inventory_conservation":True,
         "analysis_profile":"historical-no-ack" if historical else "current-ack",
         "exact_changed_targets":sorted(expected_changes),"final_owned_layers_reapplied":{
             "application_exit":application_exit,"sdk_overlay":version==S.SDK or sdk_overlay},
         "source_sha256":{name:sha((ROOT/"scripts"/name).read_bytes()) for name in (
             "patch-support-flutter-analysis.py","test-support-flutter-analysis.py",
             "patch-support-flutter-sdk-compatibility.py")}},sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PASS: real full Flutter"+version+" analyze: original4errors"+str(base_warnings+new_warnings)+
          "warnings -> pinned analysis correction0errors"+str(new_warnings)+
          "warnings -> complete owned composition0errors0warnings; "+str(fixed["info"])+" existing infos")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path(os.environ.get("RDREPO",ROOT/"rustdesk")))
    parser.add_argument("--evidence-dir",type=Path)
    parser.add_argument("--dart",type=Path)
    parser.add_argument("--bash",type=Path,help="Actual active Bash executable; required full Git Bash path on Windows")
    parser.add_argument("--flutter",type=Path)
    parser.add_argument("--analysis-checkout",type=Path,help="Read-only complete app/dependency/codegen context; verification builds an owned six-file baseline/fixed fixture")
    args=parser.parse_args()
    if bool(args.flutter)!=bool(args.analysis_checkout):
        parser.error("--flutter and --analysis-checkout must be supplied together")
    bash=branding_bash(args.bash,os.name=="nt")
    with tempfile.TemporaryDirectory(prefix="mixel-flutter-analysis-") as temporary:
        out=args.evidence_dir.resolve() if args.evidence_dir else Path(temporary).resolve()
        out.mkdir(parents=True,exist_ok=True)
        encoding_controls(out)
        captured_stdout_controls(out)
        pipeline_failure_diagnostics_controls(out)
        shell_controls(out,bash,args.source.resolve())
        files=pipeline(out,args.source.resolve(),bash)
        source_controls(out,files)
        if args.dart:
            dart_controls(out,files,args.dart)
        if args.flutter:
            analyze_app(out,args.analysis_checkout.resolve(),args.flutter,files)
    print("PASS: pinned Flutter analyzer correction qualification")


if __name__=="__main__":
    main()
