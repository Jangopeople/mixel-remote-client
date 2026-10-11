#!/usr/bin/env python3
"""Install the authoritative Mixel icon used by Flutter's actual UI loader."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--branding", required=True, type=Path)
    args = parser.parse_args()
    apply(args.repo, args.branding)
    print("PASS: Flutter toolbar and connection manager use the Mixel PNG; upstream icon fallback removed; branded footer opens Mixel")


if __name__ == "__main__":
    main()
