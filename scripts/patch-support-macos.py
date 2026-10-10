#!/usr/bin/env python3
"""Keep native permission calls available and match each Mac build's real minimum."""
import hashlib
import os
from pathlib import Path
import re

SOURCE = 'src/platform/macos.mm'
ORIGINAL_SHA256 = 'ca32cb7fd33b28faa4b452a2acc7787ff7571e7dcc756b67d2b60486a34a6bbc'
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


def patch_permissions(text):
    original = text
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
    return original.replace(OLD_CAPTURE, NEW_CAPTURE, 1).replace(OLD_INPUT, NEW_INPUT, 1)


def configure_target(build, project, pods, info, architecture):
    targets = {'aarch64': '12.3', 'x86_64': '10.14'}
    if architecture not in targets:
        raise RuntimeError('Mac architecture must be aarch64 or x86_64')
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
    changes = {repo / SOURCE: patch_permissions((repo / SOURCE).read_text())}
    if architecture is not None:
        paths = ['build.py', 'flutter/macos/Runner.xcodeproj/project.pbxproj', 'flutter/macos/Podfile']
        texts = [(repo / p).read_text() for p in paths]
        configured = configure_target(*texts, (repo / 'flutter/macos/Runner/Info.plist').read_text(), architecture)
        changes.update({repo / path: text for path, text in zip(paths, configured)})
    # Validate every selected source before writing any file.
    for path, text in changes.items():
        with path.open('w', encoding='utf-8', newline='\n') as out:
            out.write(text)
    print('   patched native Mac permission availability guards' + ('; aligned ' + architecture + ' core/Runner/advertised minimum' if architecture else ''))


if __name__ == '__main__':
    apply(Path(os.environ.get('RDREPO', './rustdesk')).resolve(), os.environ.get('MIXEL_MACOS_ARCH'))
