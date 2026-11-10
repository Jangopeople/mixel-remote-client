#!/usr/bin/env python3
"""Package the sandboxed Mixel Remote controller for macOS TestFlight; never upload."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile

from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = "ch.mixel.remote.controller"
LOCAL_SMOKE_BUNDLE = f"{BUNDLE}.local-smoke"
TEAM = "5277F8NDH4"
APP_ID = f"{TEAM}.{BUNDLE}"
DEPLOYMENT = "13.0"
ARCHITECTURES = {"aarch64-apple-darwin": "arm64", "x86_64-apple-darwin": "x86_64"}
INSTALLER = f"3rd Party Mac Developer Installer: Mixel IT and Corporate Services GmbH ({TEAM})"


def call(*argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def profile_details(path):
    result = call("/usr/bin/security", "cms", "-D", "-i", path, capture_output=True)
    profile = plistlib.loads(result.stdout)
    entitlements = profile["Entitlements"]
    expiry = profile["ExpirationDate"].replace(tzinfo=timezone.utc)
    if (
        profile["TeamIdentifier"] != [TEAM]
        or "OSX" not in profile["Platform"]
        or entitlements.get("com.apple.application-identifier") != APP_ID
        or entitlements.get("com.apple.developer.team-identifier") != TEAM
        or expiry <= datetime.now(timezone.utc)
        or "ProvisionedDevices" in profile
        or profile.get("ProvisionsAllDevices")
        or entitlements.get("com.apple.security.get-task-allow")
        or entitlements.get("get-task-allow")
    ):
        raise ValueError("Expected a current Mac App Store profile for the Mixel Remote controller")
    identities = call("/usr/bin/security", "find-identity", "-v", "-p", "codesigning", capture_output=True, text=True).stdout
    fingerprints = {
        match.group(1) for line in identities.splitlines()
        if (match := re.search(r'^\s*\d+\) ([0-9A-F]{40}) "Apple Distribution: .+ \(5277F8NDH4\)"$', line))
    }
    matching = fingerprints & {
        hashlib.sha1(cert, usedforsecurity=False).hexdigest().upper()
        for cert in profile["DeveloperCertificates"]
    }
    if len(matching) != 1:
        raise ValueError("The profile must match exactly one installed Apple Distribution private key")
    return profile, matching.pop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--service-config", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--build-number", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9]\d{0,3}(?:\.\d{1,2}){0,2}", args.build_number):
        parser.error("Use an Apple bundle build number such as 1 or 1.1")
    output = args.output.resolve()
    scratch = (ROOT / "_scratch").resolve()
    if output == scratch or not output.is_relative_to(scratch):
        parser.error("Choose a new output directory inside this repo's _scratch")
    profile_path = args.profile.resolve()
    profile, signing_identity = profile_details(profile_path)
    spec = spec_from_file_location("qa_package", ROOT / "scripts/local/package-next-macos-qa.py")
    qa = module_from_spec(spec)
    spec.loader.exec_module(qa)
    configuration = qa.public_service_configuration(args.service_config)
    output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=DEPLOYMENT)
    for key in ("MIXEL_BUILD_RELAY_FIXTURE", "CARGO_TARGET_DIR"):
        environment.pop(key, None)
    with (output / "build.log").open("w") as log:
        call("cargo", "build", "--release", "--locked", "--package", "desktop_app",
             "--features", "testflight-controller",
             *(arg for target in ARCHITECTURES for arg in ("--target", target)),
             cwd=ROOT / "next", env=environment, stdout=log, stderr=subprocess.STDOUT)
    app = output / "Mixel-Remote.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    binary = contents / "MacOS" / "Mixel-Remote"
    binary.parent.mkdir(parents=True)
    resources.mkdir()
    slices = [ROOT / "next" / "target" / target / "release" / "desktop_app" for target in ARCHITECTURES]
    call("/usr/bin/lipo", "-create", *slices, "-output", binary)
    archs = call("/usr/bin/lipo", "-archs", binary, capture_output=True, text=True).stdout.split()
    if len(archs) != len(ARCHITECTURES) or set(archs) != set(ARCHITECTURES.values()):
        raise ValueError("Expected both universal macOS slices")
    with tempfile.TemporaryDirectory(dir=output) as temp:
        iconset = Path(temp) / "Mixel-Remote.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            shutil.copyfile(ROOT / "branding" / f"icon-{size}.png", iconset / f"icon_{size}x{size}.png")
            shutil.copyfile(ROOT / "branding" / f"icon-{size * 2}.png", iconset / f"icon_{size}x{size}@2x.png")
        call("/usr/bin/iconutil", "-c", "icns", iconset, "-o", resources / "Mixel-Remote.icns")
    (resources / "service-config.json").write_bytes(configuration)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Mixel Remote",
        "CFBundleDisplayName": "Mixel Remote",
        "CFBundleIdentifier": BUNDLE,
        "CFBundleExecutable": "Mixel-Remote",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": args.build_number,
        "CFBundleIconFile": "Mixel-Remote.icns",
        "LSMinimumSystemVersion": DEPLOYMENT,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }))
    sandbox = {
        "com.apple.security.app-sandbox": True,
        "com.apple.security.network.client": True,
        # Loopback PKCE callback and WebRTC incoming peer traffic.
        "com.apple.security.network.server": True,
        "com.apple.security.files.user-selected.read-write": True,
    }
    # Never execute an ad-hoc app under the TestFlight identifier. macOS
    # otherwise creates a container owned by the ad-hoc signature and can
    # block the real TestFlight install at its first launch.
    local_entitlements = output / "local-sandbox.entitlements"
    local_entitlements.write_bytes(plistlib.dumps(sandbox))
    proof = [f"PASS: Universal architectures: {' '.join(sorted(archs))}"]
    with tempfile.TemporaryDirectory(dir=output) as temp:
        probe = Path(temp) / "Mixel-Remote-Local-Smoke.app"
        call("/usr/bin/ditto", app, probe)
        probe_info = probe / "Contents" / "Info.plist"
        info = plistlib.loads(probe_info.read_bytes())
        info["CFBundleIdentifier"] = LOCAL_SMOKE_BUNDLE
        probe_info.write_bytes(plistlib.dumps(info))
        probe_binary = probe / "Contents" / "MacOS" / binary.name
        call("/usr/bin/codesign", "--force", "--sign", "-", "--entitlements", local_entitlements, probe)
        verified = call(probe_binary, "--validate-public-config", input=configuration, capture_output=True, timeout=15)
        proof.append(verified.stdout.decode().strip())
        scope = subprocess.run([probe_binary, "--validate-identity-scope"], capture_output=True, text=True, timeout=15)
        if scope.returncode != 2 or "Controller build and signed bundle identifier do not match" not in scope.stderr:
            raise ValueError("The local sandbox must never use the TestFlight device identity")
        proof.append(f"PASS: {LOCAL_SMOKE_BUNDLE} cannot use the TestFlight device identity")
        for mode in ("--startup-host", "--prelogin-host", "--provision-prelogin-identity", "--validate-prelogin-settings"):
            rejected = subprocess.run([probe_binary, mode], capture_output=True, text=True, timeout=15)
            if rejected.returncode != 2 or "controller cannot host connections" not in rejected.stderr:
                raise ValueError(f"The controller did not refuse host mode {mode}")
            proof.append(f"PASS: isolated sandbox controller refused {mode}")

    shutil.copyfile(profile_path, contents / "embedded.provisionprofile")
    authorized_groups = profile["Entitlements"].get("keychain-access-groups", [])
    if APP_ID not in authorized_groups and f"{TEAM}.*" not in authorized_groups:
        raise ValueError("The profile does not permit the controller's private Keychain group")
    entitlements = {
        **sandbox,
        "com.apple.application-identifier": APP_ID,
        "com.apple.developer.team-identifier": TEAM,
        # Isolate the device key from every other app and native host.
        "keychain-access-groups": [APP_ID],
    }
    if profile["Entitlements"].get("beta-reports-active") is True:
        entitlements["beta-reports-active"] = True
    entitlements_path = output / "controller.entitlements"
    entitlements_path.write_bytes(plistlib.dumps(entitlements))
    call("/usr/bin/codesign", "--force", "--sign", signing_identity, "--options", "runtime",
         "--timestamp", "--entitlements", entitlements_path, app)
    call("/usr/bin/codesign", "--verify", "--deep", "--strict", "--all-architectures", app)
    signed = call("/usr/bin/codesign", "-d", "--entitlements", ":-", app, capture_output=True)
    if plistlib.loads(signed.stdout) != entitlements:
        raise ValueError("Signed entitlements differ from the controller allowlist")
    proof += [
        "PASS: Apple Mac App Store profile matches bundle ID, team and distribution private key",
        "PASS: Apple Distribution signature and sandbox entitlement allowlist verified",
    ]
    package = output / "Mixel-Remote-TestFlight.pkg"
    call("/usr/bin/productbuild", "--component", app, "/Applications", "--sign", INSTALLER, package)
    signature = call("/usr/sbin/pkgutil", "--check-signature", package, capture_output=True, text=True).stdout
    if INSTALLER not in signature:
        raise ValueError("Mac App Store installer signer does not match")
    proof += ["PASS: Mac App Store installer certificate verified", signature.strip()]
    archive = output / "Mixel-Remote.xcarchive"
    archive_app = archive / "Products" / "Applications" / app.name
    archive_app.parent.mkdir(parents=True)
    call("/usr/bin/ditto", app, archive_app)
    (archive / "Info.plist").write_bytes(plistlib.dumps({
        "ArchiveVersion": 2,
        "CreationDate": datetime.now(timezone.utc).replace(tzinfo=None),
        "Name": "Mixel Remote",
        "SchemeName": "Mixel Remote",
        "ApplicationProperties": {
            "ApplicationPath": f"Applications/{app.name}",
            "Architectures": archs,
            "CFBundleIdentifier": BUNDLE,
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": args.build_number,
            "Team": TEAM,
            "SigningIdentity": signing_identity,
        },
    }))
    (output / "TestFlight-Internal-Upload.plist").write_bytes(plistlib.dumps({
        "method": "app-store-connect",
        "destination": "upload",
        "testFlightInternalTestingOnly": True,
        "manageAppVersionAndBuildNumber": False,
        "uploadSymbols": False,
        "signingStyle": "manual",
        "teamID": TEAM,
        "signingCertificate": signing_identity,
        "installerSigningCertificate": INSTALLER,
        "provisioningProfiles": {BUNDLE: profile["UUID"]},
    }))
    call("/usr/bin/codesign", "--verify", "--strict", "--all-architectures", archive_app)
    (output / "proof.log").write_text("\n".join(proof) + "\n")
    (output / "package.json").write_text(json.dumps({
        "bundleIdentifier": BUNDLE, "appleTeam": TEAM, "build": args.build_number,
        "profileName": profile["Name"], "packageSha256": hashlib.file_digest(package.open("rb"), "sha256").hexdigest(),
        "serviceConfigurationSha256": hashlib.sha256(configuration).hexdigest(),
        "localValidation": "passed", "uploaded": False,
    }, indent=2) + "\n")
    print("\n".join(proof[:len(proof) - 1]))


if __name__ == "__main__":
    main()
