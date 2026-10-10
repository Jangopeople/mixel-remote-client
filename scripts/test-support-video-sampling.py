#!/usr/bin/env python3
"""Execute the actual Linux video sampler with strict clock and pixel controls.

The default controls need no Pillow installation or desktop. Optional retained
PNGs execute the same sampler with Pillow; a geometry oracle establishes only
pixel location, while a bracketed clock oracle separately tests capture timing.
"""
import argparse
import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
COLORS = ((229, 29, 54), (19, 183, 108), (23, 110, 233))


def extracted(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    decoder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "decode_marker")
    session = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Session")
    methods = {node.name: node for node in session.body if isinstance(node, ast.FunctionDef)}
    namespace = {"json": json, "time": SimpleNamespace(sleep=lambda _: None, monotonic=lambda: 10.0)}
    for node in (decoder, methods["video_map"], methods["fresh_video"]):
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def cells(counter):
    return [(250, 250, 250) if bit == "1" else (5, 5, 5) for bit in "1010" + format(counter, "016b")]


class PixelImage:
    def __init__(self, counter, *, tops=(0, 0, 0), popup=False, marker_covered=False, horizontal_gap=0):
        self.width, self.height = 900 + 2 * horizontal_gap, 280
        self.pixels = [[(250, 250, 250)] * self.width for _ in range(self.height)]
        for index, color in enumerate(COLORS):
            left = index * (300 + horizontal_gap)
            for y in range(tops[index], min(tops[index] + 250, self.height)):
                self.pixels[y][left:left + 300] = [color] * 300
        for index, color in enumerate(cells(counter)):
            for y in range(230, 244):
                self.pixels[y][8 + 5 * index:13 + 5 * index] = [color] * 5
        if popup:
            for y in range(32):
                self.pixels[y][:300] = [(110, 110, 110)] * 300
        if marker_covered:
            for y in range(230, 244):
                self.pixels[y][8:108] = [(110, 110, 110)] * 100

    def convert(self, mode):
        assert mode == "RGB"
        return self

    def getpixel(self, point):
        x, y = point
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError("Sample lies outside the captured image")
        return self.pixels[y][x]


def fixture(counter):
    return {"monotonic": counter / 2, "marker": {"x": 8, "y": 230, "counter": counter, "cell_width": 5, "height": 14},
            "controls": {"canvas": {"x": 0, "y": 0, "width": 900, "height": 250, "visible": True}}}


class CapturedSession:
    def __init__(self, image, before, after, decoder):
        self.image, self.before, self.after, self.decoder = image, before, after, decoder
        self.captured = False
        self.events = []

    def windows(self, role, _pattern):
        return ["owned-viewer"] if role == "controller" else []

    def gui(self, *_args):
        pass

    def activate(self, *_args):
        self.events.append("activate")

    def screenshot(self, role, name):
        assert role == "controller" and name == "sample"
        self.events.append("capture")
        self.captured = True

    def fixture_state(self):
        self.events.append("source-after" if self.captured else "source-before")
        return copy.deepcopy(self.after if self.captured else self.before)

    def run(self, role, arguments):
        assert role == "controller" and arguments[:2] == ["python3", "-c"]
        self.events.append("analyze")
        pil, image_module, probe = ModuleType("PIL"), ModuleType("PIL.Image"), ModuleType("session_probe")
        image_module.open = lambda _: self.image
        pil.Image = image_module
        probe.decode_marker = self.decoder
        replacements = {"PIL": pil, "PIL.Image": image_module, "session_probe": probe}
        previous = {name: sys.modules.get(name) for name in replacements}
        sys.modules.update(replacements)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(compile(arguments[2], "actual-video-pixel-command", "exec"), {})
            return SimpleNamespace(stdout=output.getvalue())
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


def sample(namespace, image, before, after):
    session = CapturedSession(image, before, after, namespace["decode_marker"])
    mapping, state = namespace["video_map"](session, "sample")
    assert session.events.index("capture") < session.events.index("source-after") < session.events.index("analyze")
    return mapping, state, session.events


def rejected(operation, message):
    try:
        operation()
    except (RuntimeError, AssertionError) as error:
        assert message in str(error), (message, str(error))
    else:
        raise AssertionError("Negative control was accepted: " + message)


def distinct_frames(namespace, counters, accepted):
    class Frames:
        def __init__(self):
            self.index = 0

        def video_map(self, _label):
            value = counters[min(self.index, len(counters) - 1)]
            self.index += 1
            return object(), {"marker": {"counter": value}, "decoded_marker": {"decoded_counter": value, "rectangle": [0, 0, 300]}, "video_observation": {}}

    class Output:
        def __truediv__(self, _name):
            return self

        def write_text(self, _value):
            pass

    frames = Frames()
    frames.proofs = Output()
    def bounded(_description, operation):
        for _ in range(3):
            value = operation()
            if value:
                return value
        raise RuntimeError("No distinct current frames")
    namespace["until"] = bounded
    if accepted:
        namespace["fresh_video"](frames, "phase")
    else:
        rejected(lambda: namespace["fresh_video"](frames, "phase"), "No distinct current frames")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", type=Path, default=ROOT / "scripts/test-support-session-linux.py")
    parser.add_argument("--original-harness", type=Path, help="Retained original source for actual pre-fix negative controls")
    parser.add_argument("--retained-popup-png", type=Path)
    parser.add_argument("--retained-popup-state", type=Path)
    parser.add_argument("--retained-clock-directory", type=Path)
    args = parser.parse_args()
    if bool(args.retained_popup_png) != bool(args.retained_popup_state):
        parser.error("The retained popup PNG and its independent source geometry are required together")
    current = extracted(args.harness)
    original = extracted(args.original_harness) if args.original_harness else None
    if original:
        def marker_source(path):
            return ast.dump(next(node for node in ast.parse(path.read_text()).body
                                 if isinstance(node, ast.FunctionDef) and node.name == "decode_marker"))
        assert marker_source(args.harness) == marker_source(args.original_harness), "Freshness/synchronization decoder changed"
    decode = current["decode_marker"]
    for counter, source in ((16679, 16680), (65535, 1), (0, 65535), (16679, 16685), (16679, 16677)):
        assert decode(cells(counter), source) == counter
    rejected(lambda: decode(cells(16679), 16686), "old or unrelated")
    rejected(lambda: decode(cells(16679), 16676), "old or unrelated")
    rejected(lambda: decode([(110, 110, 110)] + cells(16679)[1:], 16679), "obscured or ambiguous")
    rejected(lambda: decode([(5, 5, 5)] + cells(16679)[1:], 16679), "synchronization is absent")
    rejected(lambda: decode(cells(16679)[:-1], 16679), "invalid cell count")
    print("PASS: exact decoder retains three-second freshness, future tolerance, wrap, sync and ambiguous/covered-marker rejection")
    image = PixelImage(16679)
    _, state, events = sample(current, image, fixture(16675), fixture(16680))
    assert state["marker"]["counter"] == 16680 and state["decoded_marker"]["decoded_counter"] == 16679
    assert state["video_observation"]["source_before_capture"] == 16675
    assert events.index("capture") < events.index("source-after") < events.index("analyze")
    moved = fixture(16680)
    moved["controls"]["canvas"]["x"] += 20
    moved["marker"]["x"] += 20
    rejected(lambda: sample(current, image, fixture(16675), moved), "geometry changed across image capture")
    if original:
        rejected(lambda: sample(original, image, fixture(16675), fixture(16680)), "old or unrelated")
    print("PASS: actual sampler uses the after-capture source clock before pixel analysis, retains its tuple and rejects geometry changes across capture")
    _, state, _ = sample(current, PixelImage(16679, popup=True), fixture(16679), fixture(16679))
    assert state["decoded_marker"]["rectangle"] == [0, 0, 300]
    if original:
        rejected(lambda: sample(original, PixelImage(16679, popup=True), fixture(16679), fixture(16679)), "synchronization is absent")
    rejected(lambda: sample(current, PixelImage(16679, tops=(0, 10, 20)), fixture(16679), fixture(16679)), "RGB tops disagree")
    rejected(lambda: sample(current, PixelImage(16679, marker_covered=True), fixture(16679), fixture(16679)), "obscured or ambiguous")
    rejected(lambda: sample(current, PixelImage(16679, horizontal_gap=20), fixture(16679), fixture(16679)), "")
    print("PASS: partial red-bar popup uses the other two agreeing tops; inconsistent geometry, covered clock and separated bars fail")
    for counters, accepted in (((16679, 16680), True), ((65535, 0), True), ((16679, 16679), False), ((16679, 16799), False)):
        distinct_frames(current, counters, accepted)
    print("PASS: actual fresh_video requires two changing current observations, accepts wrap and rejects stationary/unrelated phase counters")
    if args.retained_popup_png or args.retained_clock_directory:
        from PIL import Image
    if args.retained_popup_png:
        # Independent visual/pixel oracle from the retained failing PNG. Source
        # counter is deliberately equal to its independently decoded clock:
        # this is a geometry regression, never a retained-frame freshness pass.
        observed = json.loads(args.retained_popup_state.read_text())
        observed["marker"]["counter"] = 15343
        actual = Image.open(args.retained_popup_png).convert("RGB")
        _, state, _ = sample(current, actual, observed, observed)
        assert state["decoded_marker"]["rectangle"] == [98, 163, 298]
        assert state["decoded_marker"]["decoded_counter"] == 15343
        if original:
            rejected(lambda: sample(original, actual, observed, observed), "synchronization is absent")
        print("PASS: exact retained failing popup PNG locates [98,163,298] and clock15343; original source fails sync (geometry-only oracle)")
    if args.retained_clock_directory:
        directory = args.retained_clock_directory
        before = json.loads((directory / "pre-source.json").read_text())
        after = json.loads((directory / "post-source.json").read_text())
        actual = Image.open(directory / "clock-sample.png").convert("RGB")
        _, state, _ = sample(current, actual, before, after)
        assert (before["marker"]["counter"], state["decoded_marker"]["decoded_counter"], after["marker"]["counter"]) == (16675, 16679, 16680)
        if original:
            rejected(lambda: sample(original, actual, before, after), "old or unrelated")
        print("PASS: actual desktop PNG tuple16675/16679/16680 rejects original pre-capture clock and accepts current post-capture clock (local X11, no product)")
    print("Result: exact-source video sampling controls passed; harness SHA256 " + hashlib.sha256(args.harness.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
