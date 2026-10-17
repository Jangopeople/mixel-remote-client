#!/usr/bin/env python3
"""Guard pinned Mac permissions, display metadata and native resource ownership."""
import hashlib
import os
from pathlib import Path
import re

SOURCE = 'src/platform/macos.mm'
ORIGINAL_SHA256 = 'ca32cb7fd33b28faa4b452a2acc7787ff7571e7dcc756b67d2b60486a34a6bbc'
BUILD_SOURCE = 'build.rs'
ORIGINAL_BUILD_SHA256 = '8ef5daa629f04d6d330d1080b9b5444281c538161118cc3abb1cf94f11bf25bd'
OLD_RUNTIME = '    b.flag("-std=c++17").file(file).compile("macos");\n'
NEW_RUNTIME = OLD_RUNTIME + '''    // Rust's final link omits Clang's default availability runtime. Resolve
    // the archive through the same target compiler instead of an Xcode path.
    let output = b.get_compiler().to_command()
        .arg("-print-file-name=libclang_rt.osx.a")
        .output().expect("Cannot query the Mac availability runtime");
    assert!(output.status.success(), "Mac availability runtime lookup failed");
    let archive = std::path::PathBuf::from(
        String::from_utf8(output.stdout).expect("Non-UTF8 Mac runtime path").trim()
    );
    assert!(archive.is_absolute() && archive.is_file() &&
        archive.file_name() == Some(std::ffi::OsStr::new("libclang_rt.osx.a")),
        "Target compiler did not resolve its Mac availability runtime");
    println!("cargo:rustc-link-search=native={}",
        archive.parent().unwrap().display());
    println!("cargo:rustc-link-lib=static=clang_rt.osx");
'''
OLD_CAPTURE = '''    bool res = CGPreflightScreenCaptureAccess();
    if (!res && prompt) {
        CGRequestScreenCaptureAccess();
    }
    return res;'''
NEW_CAPTURE = '''    if (@available(macOS 10.15, *)) {
        bool res = CGPreflightScreenCaptureAccess();
        if (!res && prompt) {
            CGRequestScreenCaptureAccess();
        }
        return res;
    }
    return false;'''
OLD_INPUT = '    if (floor(NSAppKitVersionNumber) >= NSAppKitVersionNumber10_15) {'
NEW_INPUT = '    if (@available(macOS 10.15, *)) {'
OLD_PIXEL_ENCODING = '\tCFStringRef pixelEncoding = CGDisplayModeCopyPixelEncoding(mode);\t\n'
NEW_PIXEL_ENCODING = OLD_PIXEL_ENCODING + '''    // Mixel: this deprecated metadata API may return no encoding.
    if (pixelEncoding == NULL) {
        return 0;
    }
'''
OLD_AUTH_FAILURE = '''    if (status != errAuthorizationSuccess) {
        printf("Failed to authorize\\n");
        return false;
    }'''
NEW_AUTH_FAILURE = '''    if (status != errAuthorizationSuccess) {
        printf("Failed to authorize\\n");
        // Mixel: cancellation/error still owns the successful-create reference.
        AuthorizationFree(authRef, kAuthorizationFlagDefaults);
        return false;
    }'''
OLD_AUTH_EXECUTE = '''        FILE *pipe = NULL;
        status = AuthorizationExecuteWithPrivileges(authRef, process, kAuthorizationFlagDefaults, args, &pipe);'''
NEW_AUTH_EXECUTE = '''        // Mixel: this caller has no stream reader; request no unused pipe.
        status = AuthorizationExecuteWithPrivileges(authRef, process, kAuthorizationFlagDefaults, args, NULL);'''

# Closed authorization-only replacement. The former C execution helper has no
# non-NULL application caller; Rust's actual elevation path remains independent.
OLD_AUTHORIZATION = r'''extern "C" bool Elevate(char* process, char** args) {
    AuthorizationRef authRef;
    OSStatus status;

    status = AuthorizationCreate(NULL, kAuthorizationEmptyEnvironment,
                                kAuthorizationFlagDefaults, &authRef);
    if (status != errAuthorizationSuccess) {
        printf("Failed to create AuthorizationRef\n");
        return false;
    }

    AuthorizationItem authItem = {kAuthorizationRightExecute, 0, NULL, 0};
    AuthorizationRights authRights = {1, &authItem};
    AuthorizationFlags flags = kAuthorizationFlagDefaults |
                                kAuthorizationFlagInteractionAllowed |
                                kAuthorizationFlagPreAuthorize |
                                kAuthorizationFlagExtendRights;
    status = AuthorizationCopyRights(authRef, &authRights, kAuthorizationEmptyEnvironment, flags, NULL);
    if (status != errAuthorizationSuccess) {
        printf("Failed to authorize\n");
        return false;
    }

    if (process != NULL) {
        FILE *pipe = NULL;
        status = AuthorizationExecuteWithPrivileges(authRef, process, kAuthorizationFlagDefaults, args, &pipe);
        if (status != errAuthorizationSuccess) {
            printf("Failed to run as root\n");
            AuthorizationFree(authRef, kAuthorizationFlagDefaults);
            return false;
        }
    }

    AuthorizationFree(authRef, kAuthorizationFlagDefaults);
    return true;
}

extern "C" bool MacCheckAdminAuthorization() {
    return Elevate(NULL, NULL);
}'''
NEW_AUTHORIZATION = r'''static bool CheckAdminAuthorization() {
    AuthorizationRef authRef;
    OSStatus status;

    status = AuthorizationCreate(NULL, kAuthorizationEmptyEnvironment,
                                kAuthorizationFlagDefaults, &authRef);
    if (status != errAuthorizationSuccess) {
        printf("Failed to create AuthorizationRef\n");
        return false;
    }

    AuthorizationItem authItem = {kAuthorizationRightExecute, 0, NULL, 0};
    AuthorizationRights authRights = {1, &authItem};
    AuthorizationFlags flags = kAuthorizationFlagDefaults |
                                kAuthorizationFlagInteractionAllowed |
                                kAuthorizationFlagPreAuthorize |
                                kAuthorizationFlagExtendRights;
    status = AuthorizationCopyRights(authRef, &authRights, kAuthorizationEmptyEnvironment, flags, NULL);
    if (status != errAuthorizationSuccess) {
        printf("Failed to authorize\n");
        // Mixel: cancellation/error still owns the successful-create reference.
        AuthorizationFree(authRef, kAuthorizationFlagDefaults);
        return false;
    }

    AuthorizationFree(authRef, kAuthorizationFlagDefaults);
    return true;
}

extern "C" bool MacCheckAdminAuthorization() {
    return CheckAdminAuthorization();
}'''

OLD_CONFIG_HELPER = 'static bool setDisplayToMode(CGDirectDisplayID display, CGDisplayModeRef mode) {\n    CGError rc;\n    CGDisplayConfigRef config;\n    rc = CGBeginDisplayConfiguration(&config);\n    if (rc != kCGErrorSuccess) {\n        return false;\n    }\n    rc = CGConfigureDisplayWithDisplayMode(config, display, mode, NULL);\n    if (rc != kCGErrorSuccess) {\n        return false;\n    }\n    rc = CGCompleteDisplayConfiguration(config, kCGConfigureForSession);\n    if (rc != kCGErrorSuccess) {\n        return false;\n    }\n    return true;\n}'
NEW_CONFIG_HELPER = 'static bool setDisplayToMode(CGDirectDisplayID display, CGDisplayModeRef mode) {\n    CGError rc;\n    CGDisplayConfigRef config;\n    rc = CGBeginDisplayConfiguration(&config);\n    if (rc != kCGErrorSuccess) {\n        return false;\n    }\n    rc = CGConfigureDisplayWithDisplayMode(config, display, mode, NULL);\n    if (rc != kCGErrorSuccess) {\n        // Begin succeeded; only this failure path still owns the config.\n        CGCancelDisplayConfiguration(config);\n        return false;\n    }\n    rc = CGCompleteDisplayConfiguration(config, kCGConfigureForSession);\n    if (rc != kCGErrorSuccess) {\n        return false;\n    }\n    return true;\n}'

OLD_MODE_HELPER = '// https://github.com/jhford/screenresolution/blob/master/cg_utils.c\n// https://github.com/jdoupe/screenres/blob/master/setgetscreen.m\n\nsize_t bitDepth(CGDisplayModeRef mode) {\n    size_t depth = 0;\n    // Deprecated, same display same bpp? \n    // https://stackoverflow.com/questions/8210824/how-to-avoid-cgdisplaymodecopypixelencoding-to-get-bpp\n    // https://github.com/libsdl-org/SDL/pull/6628\n\tCFStringRef pixelEncoding = CGDisplayModeCopyPixelEncoding(mode);\t\n    // my numerical representation for kIO16BitFloatPixels and kIO32bitFloatPixels\t\n    // are made up and possibly non-sensical\t\n    if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(kIO32BitFloatPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 96;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(kIO64BitDirectPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 64;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(kIO16BitFloatPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 48;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(IO32BitDirectPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 32;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(kIO30BitDirectPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 30;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(IO16BitDirectPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 16;\t\n    } else if (kCFCompareEqualTo == CFStringCompare(pixelEncoding, CFSTR(IO8BitIndexedPixels), kCFCompareCaseInsensitive)) {\t\n        depth = 8;\t\n    }\t\n    CFRelease(pixelEncoding);\t\n    return depth;\t\n}'
NEW_MODE_HELPER = "// Display suitability is independent of the capture stream's BGRA output.\nstatic bool isUsableDisplayMode(CGDisplayModeRef mode) {\n    return mode != NULL && CGDisplayModeIsUsableForDesktopGUI(mode);\n}"
OLD_MODE_FILTER = 'bitDepth(currentMode) == bitDepth(mode)'
NEW_MODE_FILTER = 'isUsableDisplayMode(mode)'


def patch_permissions(text):
    original = text
    if original.count(NEW_CONFIG_HELPER) == 1:
        original = original.replace(NEW_CONFIG_HELPER, OLD_CONFIG_HELPER, 1)
    if original.count(NEW_MODE_HELPER) == 1:
        original = original.replace(NEW_MODE_HELPER, OLD_MODE_HELPER, 1)
    if original.count(NEW_MODE_FILTER) == 2:
        original = original.replace(NEW_MODE_FILTER, OLD_MODE_FILTER, 2)
    if original.count(NEW_AUTHORIZATION) == 1:
        original = original.replace(NEW_AUTHORIZATION, OLD_AUTHORIZATION, 1)
    # Accept only exact prior owned failure/pipe fixes before the full upstream
    # hash check, so an existing corrected checkout upgrades without drift.
    for old, new in ((OLD_PIXEL_ENCODING, NEW_PIXEL_ENCODING),
                     (OLD_AUTH_FAILURE, NEW_AUTH_FAILURE),
                     (OLD_AUTH_EXECUTE, NEW_AUTH_EXECUTE)):
        if original.count(new) == 1:
            original = original.replace(new, old, 1)
    if text.count(NEW_CAPTURE) == 1:
        original = original.replace(NEW_CAPTURE, OLD_CAPTURE, 1)
    # NEW_INPUT occurs in both corrected permission functions; normalize only
    # the original InputMonitoringAuthStatus block after capture normalization.
    start = original.find('extern "C" bool InputMonitoringAuthStatus(bool prompt) {')
    if start < 0:
        raise RuntimeError('Pinned input permission function changed')
    if original[start:].count(NEW_INPUT) == 1:
        original = original[:start] + original[start:].replace(NEW_INPUT, OLD_INPUT, 1)
    if hashlib.sha256(original.encode()).hexdigest() != ORIGINAL_SHA256:
        raise RuntimeError('Pinned 1.4.6 Mac permission source changed')
    if original.count(OLD_CAPTURE) != 1 or original.count(OLD_INPUT) != 1:
        raise RuntimeError('Pinned Mac permission call anchors changed')
    fixed = original.replace(OLD_CAPTURE, NEW_CAPTURE, 1).replace(OLD_INPUT, NEW_INPUT, 1)
    for old, new in ((OLD_MODE_HELPER, NEW_MODE_HELPER),
                     (OLD_CONFIG_HELPER, NEW_CONFIG_HELPER),
                     (OLD_AUTHORIZATION, NEW_AUTHORIZATION)):
        if fixed.count(old) != 1:
            raise RuntimeError('Pinned Mac native failure boundary changed')
        fixed = fixed.replace(old, new, 1)
    if fixed.count(OLD_MODE_FILTER) != 2:
        raise RuntimeError("Pinned Mac display mode filter changed")
    return fixed.replace(OLD_MODE_FILTER, NEW_MODE_FILTER, 2)


def patch_runtime(text):
    original = text.replace(NEW_RUNTIME, OLD_RUNTIME, 1) if text.count(NEW_RUNTIME) == 1 else text
    if hashlib.sha256(original.encode()).hexdigest() != ORIGINAL_BUILD_SHA256:
        raise RuntimeError('Pinned 1.4.6 Mac build script changed')
    if original.count(OLD_RUNTIME) != 1:
        raise RuntimeError('Pinned Mac compiler anchor changed')
    return original.replace(OLD_RUNTIME, NEW_RUNTIME, 1)


ASSEMBLE_TARGET = '\t\t33CC111A2044C6BA0003C045 /* Flutter Assemble */ = {\n\t\t\tisa = PBXAggregateTarget;\n\t\t\tbuildConfigurationList = 33CC111B2044C6BA0003C045 /* Build configuration list for PBXAggregateTarget "Flutter Assemble" */;\n\t\t\tbuildPhases = (\n\t\t\t\t33CC111E2044C6BF0003C045 /* ShellScript */,\n\t\t\t);\n\t\t\tdependencies = (\n\t\t\t);\n\t\t\tname = "Flutter Assemble";\n\t\t\tproductName = FLX;\n\t\t};\n'
OLD_ASSEMBLE_PHASE = '\t\t33CC111E2044C6BF0003C045 /* ShellScript */ = {\n\t\t\tisa = PBXShellScriptBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);\n\t\t\tinputFileListPaths = (\n\t\t\t\tFlutter/ephemeral/FlutterInputs.xcfilelist,\n\t\t\t);\n\t\t\tinputPaths = (\n\t\t\t\tFlutter/ephemeral/tripwire,\n\t\t\t);\n\t\t\toutputFileListPaths = (\n\t\t\t\tFlutter/ephemeral/FlutterOutputs.xcfilelist,\n\t\t\t);\n\t\t\toutputPaths = (\n\t\t\t);\n\t\t\trunOnlyForDeploymentPostprocessing = 0;\n\t\t\tshellPath = /bin/sh;\n\t\t\tshellScript = "\\"$FLUTTER_ROOT\\"/packages/flutter_tools/bin/macos_assemble.sh && touch Flutter/ephemeral/tripwire";\n\t\t};\n'
NEW_ASSEMBLE_PHASE = '\t\t33CC111E2044C6BF0003C045 /* ShellScript */ = {\n\t\t\tisa = PBXShellScriptBuildPhase;\n\t\t\talwaysOutOfDate = 1;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);\n\t\t\tinputFileListPaths = (\n\t\t\t\tFlutter/ephemeral/FlutterInputs.xcfilelist,\n\t\t\t);\n\t\t\tinputPaths = (\n\t\t\t\tFlutter/ephemeral/tripwire,\n\t\t\t);\n\t\t\toutputFileListPaths = (\n\t\t\t\tFlutter/ephemeral/FlutterOutputs.xcfilelist,\n\t\t\t);\n\t\t\toutputPaths = (\n\t\t\t);\n\t\t\trunOnlyForDeploymentPostprocessing = 0;\n\t\t\tshellPath = /bin/sh;\n\t\t\tshellScript = "\\"$FLUTTER_ROOT\\"/packages/flutter_tools/bin/macos_assemble.sh && touch Flutter/ephemeral/tripwire";\n\t\t};\n'

def patch_assemble_phase(project):
    eol = "\r\n" if "\r\n" in project else "\n"
    logical = project.replace("\r\n", "\n")
    if "\r" in logical or (eol == "\r\n" and "\n" in project.replace("\r\n", "")):
        raise RuntimeError("Mixed Runner project newlines")
    if logical.count(ASSEMBLE_TARGET) != 1 or logical.count("33CC111E2044C6BF0003C045") != 2:
        raise RuntimeError("Pinned Flutter Assemble target binding changed")
    original = logical.replace(NEW_ASSEMBLE_PHASE, OLD_ASSEMBLE_PHASE, 1) if logical.count(NEW_ASSEMBLE_PHASE) == 1 else logical
    section = re.findall(r"/\* Begin PBXShellScriptBuildPhase section \*/(.*?)/\* End PBXShellScriptBuildPhase section \*/", original, re.S)
    if len(section) != 1 or section[0].count(OLD_ASSEMBLE_PHASE) != 1 or original.count(OLD_ASSEMBLE_PHASE) != 1:
        raise RuntimeError("Pinned Flutter Assemble script phase changed")
    return original.replace(OLD_ASSEMBLE_PHASE, NEW_ASSEMBLE_PHASE, 1).replace("\n", eol)


def configure_target(build, project, pods, info, architecture):
    targets = {'aarch64': '12.3', 'x86_64': '10.14'}
    if architecture not in targets:
        raise RuntimeError('Mac architecture must be aarch64 or x86_64')
    project = patch_assemble_phase(project)
    minimum = targets[architecture]
    if re.findall(r'MACOSX_DEPLOYMENT_TARGET=([0-9.]+)', build) not in [['10.14'], ['12.3']]:
        raise RuntimeError('Pinned Mac core deployment target changed')
    current = re.findall(r'MACOSX_DEPLOYMENT_TARGET = ([0-9.]+);', project)
    if len(current) != 6 or len(set(current)) != 1 or current[0] not in ('10.14', '12.3'):
        raise RuntimeError('Pinned Runner deployment targets changed')
    if re.findall(r"^platform :osx, '([0-9.]+)'$", pods, re.M) not in [['10.14'], ['12.3']]:
        raise RuntimeError('Pinned CocoaPods minimum changed')
    if info.count('<key>LSMinimumSystemVersion</key>\n\t<string>$(MACOSX_DEPLOYMENT_TARGET)</string>') != 1:
        raise RuntimeError('Runner minimum OS metadata no longer follows its target')
    return (
        re.sub(r'MACOSX_DEPLOYMENT_TARGET=(?:10\.14|12\.3)', 'MACOSX_DEPLOYMENT_TARGET=' + minimum, build),
        re.sub(r'MACOSX_DEPLOYMENT_TARGET = (?:10\.14|12\.3);', 'MACOSX_DEPLOYMENT_TARGET = ' + minimum + ';', project),
        re.sub(r"^platform :osx, '(?:10\.14|12\.3)'$", "platform :osx, '" + minimum + "'", pods, flags=re.M),
    )


def apply(repo, architecture=None):
    manifest = (repo / 'Cargo.toml').read_text()
    package = re.search(r'(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)', manifest)
    if re.findall(r'^version\s*=\s*"([^"]+)"\s*$', package.group(1) if package else '', re.M) != ['1.4.6']:
        raise RuntimeError('Mac corrections require pinned upstream 1.4.6')
    changes = {
        repo / SOURCE: patch_permissions((repo / SOURCE).read_text()),
        repo / BUILD_SOURCE: patch_runtime((repo / BUILD_SOURCE).read_text()),
    }
    if architecture is not None:
        paths = ['build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile']
        texts = [(repo / p).read_bytes().decode('utf-8') if p.endswith('project.pbxproj') else (repo / p).read_text() for p in paths]
        configured = configure_target(*texts, (repo / 'flutter/macos/Runner/Info.plist').read_text(), architecture)
        changes.update({repo / path: text for path, text in zip(paths, configured)})
    # Validate every selected source before writing any file.
    for path, text in changes.items():
        with path.open('w', encoding='utf-8', newline='\n') as out:
            out.write(text)
    print('   patched native Mac permission availability, desktop-usable display modes and authorization ownership' + ('; aligned ' + architecture + ' core/Runner/advertised minimum' if architecture else ''))


if __name__ == '__main__':
    apply(Path(os.environ.get('RDREPO', './rustdesk')).resolve(), os.environ.get('MIXEL_MACOS_ARCH'))
