#!/usr/bin/env python3
"""Prepare the branded Flutter UI and its Windows consent startup."""
import argparse
import hashlib
from pathlib import Path

UPSTREAM_ICON_SHA256 = "4d6ecc2b45571382576049095435f41576c02e895b8766ef3f300623c35b3488"
ORIGINAL = """Widget loadIcon(double size) {
  return Image.asset('assets/icon.png',
      width: size,
      height: size,
      errorBuilder: (ctx, error, stackTrace) => SvgPicture.asset(
            'assets/icon.svg',
            width: size,
            height: size,
          ));
}"""
BRANDED = """Widget loadIcon(double size) {
  return Image.asset('assets/icon.png', width: size, height: size);
}"""


FOOTER_ORIGINAL = """Widget loadPowered(BuildContext context) {
  if (bind.mainGetBuildinOption(key: "hide-powered-by-me") == 'Y') {
    return SizedBox.shrink();
  }
  return MouseRegion(
    cursor: SystemMouseCursors.click,
    child: GestureDetector(
      onTap: () {
        launchUrl(Uri.parse('https://rustdesk.com'));
      },
      child: Opacity(
          opacity: 0.5,
          child: Text(
            translate("powered_by_me"),
            overflow: TextOverflow.clip,
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(fontSize: 9, decoration: TextDecoration.underline),
          )),
    ),
  ).marginOnly(top: 6);
}"""
FOOTER_BRANDED = FOOTER_ORIGINAL.replace("https://rustdesk.com", "https://mixel.ch")


def apply(repo: Path, branding: Path) -> None:
    common = repo / "flutter/lib/common.dart"
    assets = repo / "flutter/assets"
    icon, previous = assets / "icon.png", assets / "icon.svg"
    expected = (branding / "icon-512.png").read_bytes()
    if not expected.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Authoritative Mixel icon must be a PNG")
    source = common.read_bytes().decode("utf-8")
    old_variants = [ORIGINAL, ORIGINAL.replace("\n", "\r\n")]
    new_variants = [BRANDED, BRANDED.replace("\n", "\r\n")]
    original = sum(source.count(value) for value in old_variants)
    branded = sum(source.count(value) for value in new_variants)
    if (original, branded) not in ((1, 0), (0, 1)):
        raise ValueError("Pinned Flutter icon loader changed")
    footer_old = [FOOTER_ORIGINAL, FOOTER_ORIGINAL.replace("\n", "\r\n")]
    footer_new = [FOOTER_BRANDED, FOOTER_BRANDED.replace("\n", "\r\n")]
    footer_original = sum(source.count(value) for value in footer_old)
    footer_branded = sum(source.count(value) for value in footer_new)
    if (footer_original, footer_branded) not in ((1, 0), (0, 1)):
        raise ValueError("Pinned Flutter support footer changed")
    if previous.is_symlink() or icon.is_symlink() or common.is_symlink() or assets.is_symlink():
        raise ValueError("Flutter branding paths must be owned regular paths")
    if previous.exists():
        if not previous.is_file() or hashlib.sha256(previous.read_bytes()).hexdigest() != UPSTREAM_ICON_SHA256:
            raise ValueError("Pinned upstream Flutter icon changed")
    elif original:
        raise ValueError("Pinned upstream Flutter icon is missing")
    if icon.exists() and (not icon.is_file() or icon.read_bytes() != expected):
        raise ValueError("Unexpected existing Flutter icon")
    # Validate all inputs before replacing the loader or its assets. The PNG
    # uses the normal Flutter decoder, with no upstream SVG fallback. Missing
    # or altered compiled assets are rejected by verify-flutter-branding.py.
    icon.write_bytes(expected)
    if original:
        index = next(i for i, value in enumerate(old_variants) if value in source)
        source = source.replace(old_variants[index], new_variants[index], 1)
    if footer_original:
        index = next(i for i, value in enumerate(footer_old) if value in source)
        source = source.replace(footer_old[index], footer_new[index], 1)
    if original or footer_original:
        common.write_bytes(source.encode("utf-8"))
    if previous.exists():
        previous.unlink()


INCLUDE_BEFORE = '#include "flutter_window.h"\n'
INCLUDE_AFTER = INCLUDE_BEFORE + '#include "mixel_consent_display.h"\n'
START_BEFORE = '  command_line_arguments.insert(command_line_arguments.end(), rust_args.begin(), rust_args.end());\n'
START_AFTER = (
    '  if (is_cm_page && !command_line_arguments.empty() &&\n'
    '      command_line_arguments.front() == "--cm") {\n'
    '    mixel::consent_display::Prepare();\n'
    '  }\n'
    + START_BEFORE
)


def patched_runner(source: str) -> str:
    if source.count(INCLUDE_AFTER) == source.count(START_AFTER) == 1:
        return source
    if (INCLUDE_AFTER in source or START_AFTER in source
            or source.count(INCLUDE_BEFORE) != 1 or source.count(START_BEFORE) != 1):
        raise ValueError("Windows consent runner anchors changed")
    return source.replace(INCLUDE_BEFORE, INCLUDE_AFTER).replace(START_BEFORE, START_AFTER)


def prepare_windows_runner(repo: Path, branding: Path) -> tuple[Path, bytes, bytes]:
    runner = repo / "flutter/windows/runner"
    main = runner / "main.cpp"
    header = runner / "mixel_consent_display.h"
    if main.is_symlink() or header.is_symlink() or not main.is_file():
        raise ValueError("Windows consent runner must be an owned regular file")
    original = main.read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    normalized = original.replace(b"\r\n", b"\n")
    if b"\r" in normalized or normalized.replace(b"\n", newline) != original:
        raise ValueError("Windows consent runner has mixed line endings")
    body = patched_runner(normalized.decode("utf-8")).encode("utf-8").replace(b"\n", newline)
    helper = (branding / "windows/consent_display.h").read_bytes()
    if header.exists() and header.read_bytes() != helper:
        raise ValueError("The generated Windows display helper has unexpected changes")
    return main, body, helper


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--branding", required=True, type=Path)
    args = parser.parse_args()
    runner, runner_bytes, helper_bytes = prepare_windows_runner(args.repo, args.branding)
    apply(args.repo, args.branding)
    runner.write_bytes(runner_bytes)
    runner.with_name("mixel_consent_display.h").write_bytes(helper_bytes)
    print("PASS: Flutter toolbar and connection manager use the Mixel PNG; upstream icon fallback removed; branded footer opens Mixel")


if __name__ == "__main__":
    main()
