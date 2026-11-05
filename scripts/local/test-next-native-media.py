#!/usr/bin/env python3
"""Prove actual hardware H264/DTLS-SRTP with an independent decoder.

Uses isolated loopback peers and generated colour fixtures, not customer sessions.
Requires a Mac hardware encoder plus ffmpeg/ffprobe; never substitutes a mock.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location("workspace_qa", ROOT / "scripts/local/test-next-workspace.py")
workspace_qa = module_from_spec(spec)
spec.loader.exec_module(workspace_qa)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    scratch = (ROOT / "_scratch").resolve()
    if output == scratch or not output.is_relative_to(scratch):
        parser.error("Use a new evidence directory inside this repository's _scratch")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        parser.error("ffmpeg and ffprobe are required for independent verification")
    output.mkdir(parents=True, exist_ok=False)
    before = workspace_qa.inventory()
    before[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result = {"qualified": False, "scope": "native hardware media, not GUI acceptance", "source": before}
    try:
        environment = dict(os.environ, MIXEL_MEDIA_PROOF_DIR=str(output / "transport"))
        command = ["cargo", "test", "--manifest-path", str(ROOT / "next/Cargo.toml"),
                   "-p", "native_os_capture", "--test", "media_transport", "--offline", "--", "--nocapture"]
        with (output / "transport.log").open("w") as log:
            subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=180)
        stream = output / "transport/received.h264"
        probe = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                                "-show_entries", "stream=width,height,nb_read_frames,color_space,color_transfer,color_primaries",
                                "-of", "json", str(stream)], capture_output=True, check=True, timeout=30)
        metadata = json.loads(probe.stdout)["streams"][0]
        if (metadata.get("width"), metadata.get("height"), metadata.get("nb_read_frames"),
                metadata.get("color_space"), metadata.get("color_transfer"), metadata.get("color_primaries")) != (
                    640, 420, "12", "bt709", "iec61966-2-1", "bt709"):
            raise RuntimeError("Unexpected decoded dimensions, count or colour metadata")
        decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(stream), "-f", "rawvideo",
                                  "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True, timeout=30)
        frame_size = 640 * 420 * 3
        if len(decoded.stdout) != 12 * frame_size:
            raise RuntimeError("Independent decoder did not emit exactly twelve complete frames")
        headers = subprocess.run([ffmpeg, "-hide_banner", "-i", str(stream), "-c:v", "copy", "-bsf:v",
                                  "trace_headers", "-f", "null", "-"], capture_output=True, check=True, timeout=30)
        header_text = headers.stderr.decode("utf8")
        (output / "headers.log").write_text(header_text)
        for field, expected in [("bitstream_restriction_flag", "1"), ("max_num_reorder_frames", "0"),
                                ("max_dec_frame_buffering", "1")]:
            values = re.findall(r"\b" + field + r"\s+[^=]+?=\s*(\d+)\s*$", header_text, re.MULTILINE)
            if not values or any(value != expected for value in values):
                raise RuntimeError("Incorrect or missing low-delay SPS field: " + field)
        samples = []
        for frame in range(12):
            for x, expected in [(160, [16, 208, 80]), (480, [32, 64, 220])]:
                offset = frame * frame_size + (210 * 640 + x) * 3
                pixel = list(decoded.stdout[offset:offset + 3])
                error = max(abs(a - b) for a, b in zip(pixel, expected))
                if error > 3:
                    raise RuntimeError(f"Frame {frame}, x={x}: RGB {pixel}, expected {expected}")
                samples.append({"frame": frame, "x": x, "rgb": pixel, "maximumChannelError": error})
        after = workspace_qa.inventory()
        after[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if before != after:
            raise RuntimeError("Source changed during media qualification")
        result.update(qualified=True, sourceUnchanged=True, zeroReorderMetadata=True, probe=metadata, samples=samples,
                      receivedSha256=hashlib.sha256(stream.read_bytes()).hexdigest(),
                      decodedFrames=12, decodedBytes=len(decoded.stdout))
        print("PASS: hardware H264, authenticated DTLS-SRTP, consent/certificate rejection; 12 independently decoded frames; 24 colour checks")
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
