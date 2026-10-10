#!/usr/bin/env python3
"""Collect one exact Linux build artifact for native, publication-free QA."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import zipfile

REPOSITORY = "Jangopeople/mixel-remote-client"
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_PACKAGE = 256 * 1024 * 1024


def validate_run(run, expected_run):
    expected_run = str(expected_run)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", expected_run):
        raise ValueError("Expected run must be a canonical positive decimal ID")
    url = f"https://github.com/{REPOSITORY}/actions/runs/{expected_run}"
    if type(run.get("id")) is not int or str(run["id"]) != expected_run:
        raise ValueError("GitHub returned a different build run")
    if run.get("repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("Build belongs to a different repository")
    if run.get("event") != "workflow_dispatch" or run.get("path") != ".github/workflows/build.yml":
        raise ValueError("Expected a dispatched client build workflow")
    if run.get("html_url") != url or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")):
        raise ValueError("Build URL/source attribution is invalid")
    if run.get("status") not in ("completed", "in_progress"):
        raise ValueError("Build has no collectable completed artifact")
    return run["head_sha"]


def select_linux_artifact(artifacts, run, source):
    matches = [a for a in artifacts if a.get("name") == "linux"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Linux artifact")
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ValueError("Linux artifact is expired or lacks expiry attribution")
    if type(artifact.get("id")) is not int or artifact["id"] < 1:
        raise ValueError("Linux artifact ID is invalid")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")):
        raise ValueError("GitHub archive digest is missing or invalid")
    if type(artifact.get("size_in_bytes")) is not int or not 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE:
        raise ValueError("Linux artifact exceeds the bounded download size")
    origin = artifact.get("workflow_run", {})
    if type(origin.get("id")) is not int or origin["id"] != int(run) or origin.get("head_sha") != source:
        raise ValueError("Linux artifact belongs to a different build/source")
    return artifact


def read_linux_package(archive, expected_digest):
    archive = Path(archive)
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("Downloaded archive exceeds its bound")
    actual = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected_digest:
        raise ValueError("Actual archive bytes differ from GitHub's artifact digest")
    with zipfile.ZipFile(archive) as contents:
        files = [entry for entry in contents.infolist() if not entry.is_dir()]
        if len(files) != 1:
            raise ValueError("Linux archive must contain exactly one installer")
        entry = files[0]
        if not re.fullmatch(r"mixel-remote-[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?-x86_64\.deb", entry.filename):
            raise ValueError("Linux installer name/path is not canonical")
        mode = entry.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
            raise ValueError("Linux installer must be a regular unencrypted file")
        if not 0 < entry.file_size <= MAX_PACKAGE:
            raise ValueError("Linux installer exceeds its bounded size")
        package = contents.read(entry)
    return entry.filename, package, actual


def api_json(path):
    result = subprocess.run(["gh", "api", "--hostname", "github.com", path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError("Read-only GitHub artifact metadata request failed")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", args.run_id):
        parser.error("Expected run must be a canonical positive decimal ID")
    root = args.output.resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    run = api_json(f"repos/{REPOSITORY}/actions/runs/{args.run_id}")
    source = validate_run(run, args.run_id)
    # Follow every page; never accept a truncated list hiding duplicates.
    artifacts = []
    page = 1
    while True:
        response = api_json(f"repos/{REPOSITORY}/actions/runs/{args.run_id}/artifacts?per_page=100&page={page}")
        batch = response["artifacts"]
        artifacts.extend(batch)
        if len(artifacts) >= response["total_count"]:
            break
        if not batch or page >= 20:
            raise ValueError("Artifact inventory is incomplete or exceeds its bound")
        page += 1
    artifact = select_linux_artifact(artifacts, args.run_id, source)
    archive = root / "linux-artifact.zip"
    # gh scopes authentication to GitHub while following the signed download
    # redirect. Never forward a bearer header using a generic redirect client.
    with archive.open("xb") as output:
        result = subprocess.run(["gh", "api", "--hostname", "github.com",
                                 f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
                                stdout=output, stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise RuntimeError("Read-only exact artifact archive request failed")
    name, package, digest = read_linux_package(archive, artifact["digest"])
    relative = Path("artifacts/linux") / name
    (root / relative).parent.mkdir(parents=True)
    (root / relative).write_bytes(package)
    sha256 = hashlib.sha256(package).hexdigest()
    (root / "run-metadata.json").write_text(json.dumps(run, indent=2) + "\n")
    provenance = {"repository": REPOSITORY, "build_run_id": int(args.run_id),
                  "build_url": run["html_url"], "event": run["event"],
                  "head_sha": source, "source_commit": source,
                  "application_source_baseline": source,
                  "build_status": run["status"], "build_conclusion": run["conclusion"],
                  "publication": False,
                  "collected_artifacts": {"linux": {"artifact_id": artifact["id"],
                      "artifact_digest": artifact["digest"], "actual_archive_digest": digest,
                      "files_sha256": {relative.as_posix(): sha256}}}}
    (root / "artifact-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a") as output:
            for key, value in {"artifact_root": str(root), "deb": str(root / relative),
                               "source_commit": source, "sha256": sha256}.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("Invalid workflow output path")
                output.write(f"{key}={value}\n")
    print(f"PASS: exact Linux artifact {artifact['id']} archive digest matches GitHub; run {args.run_id}; app source {source}; DEB SHA256 {sha256}")
    print(f"Overall build status/conclusion preserved: {run['status']}/{run['conclusion']}; this is scoped artifact provenance, not release success")


if __name__ == "__main__":
    main()
