#!/usr/bin/env python3
"""Generate every PULSE icon directly from assets/pulse-icon-source.png."""

from __future__ import annotations

import argparse
import base64
import io
import struct
import sys
from pathlib import Path

from PIL import Image

SOURCE_IMAGE = "assets/pulse-icon-source.png"

# (repository path, output kind, dimensions)
TARGETS: list[tuple[str, str, object]] = [
    ("assets/icon-master.svg", "svg", 1024),
    ("assets/icon-master-dark.svg", "svg", 1024),
    ("apps/desktop/assets/icon.png", "png", 1024),
    ("apps/desktop/assets/icon.ico", "ico", [16, 24, 32, 48, 64, 128, 256]),
    ("apps/desktop/assets/icon.icns", "icns", [16, 32, 64, 128, 256, 512, 1024]),
    ("apps/desktop/assets/icon-dark.png", "png", 1024),
    ("apps/desktop/assets/icon-dark.ico", "ico", [16, 24, 32, 48, 64, 128, 256]),
    ("apps/desktop/assets/icon-dark.icns", "icns", [16, 32, 64, 128, 256, 512, 1024]),
    ("apps/desktop/assets/appx/Wide310x150Logo.png", "wide", (310, 150)),
    ("apps/desktop/assets/appx/StoreLogo.png", "png", 50),
    ("apps/desktop/assets/appx/Square44x44Logo.png", "png", 44),
    ("apps/desktop/assets/appx/Square150x150Logo.png", "png", 150),
    ("apps/desktop/assets/appx/Wide310x150Logo-dark.png", "wide", (310, 150)),
    ("apps/desktop/assets/appx/StoreLogo-dark.png", "png", 50),
    ("apps/desktop/assets/appx/Square44x44Logo-dark.png", "png", 44),
    ("apps/desktop/assets/appx/Square150x150Logo-dark.png", "png", 150),
    ("apps/desktop/public/apple-touch-icon.png", "png", 1024),
    ("apps/desktop/assets/icon-mac.png", "png", 1024),
    ("apps/desktop/public/pulse-icon.png", "png", 256),
    ("apps/bootstrap-installer/src-tauri/icons/32x32.png", "png", 32),
    ("apps/bootstrap-installer/src-tauri/icons/128x128.png", "png", 128),
    ("apps/bootstrap-installer/src-tauri/icons/128x128@2x.png", "png", 256),
    ("apps/bootstrap-installer/src-tauri/icons/icon.ico", "ico", [16, 32, 64, 128, 256]),
    ("apps/bootstrap-installer/src-tauri/icons/icon.icns", "icns", [16, 32, 64, 128, 256, 512, 1024]),
    ("apps/bootstrap-installer/public/pulse-icon.png", "png", 256),
    ("website/static/img/logo.png", "contain", (1772, 1799)),
    ("website/static/img/logo-dark.png", "contain", (1772, 1799)),
    ("website/static/img/pulse-logo.png", "png", 150),
    ("website/static/img/pulse-logo-dark.png", "png", 150),
    ("website/static/img/favicon-16x16.png", "png", 16),
    ("website/static/img/favicon-32x32.png", "png", 32),
    ("website/static/img/apple-touch-icon.png", "png", 180),
    ("website/static/img/favicon.ico", "ico", [16, 32, 48]),
    ("website/static/img/favicon.svg", "svg", 1024),
    ("web/public/favicon.ico", "ico", [16, 32, 48]),
]


def load_source(source: Path) -> Image.Image:
    path = source / SOURCE_IMAGE
    try:
        with Image.open(path) as image:
            image.load()
            if image.width != image.height:
                raise ValueError(f"{path}: expected a square source image, got {image.size}")
            return image.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot load icon source {path}: {exc}") from exc


def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.resize(size, Image.Resampling.LANCZOS)


def save_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def svg_bytes(image: Image.Image, size: int) -> bytes:
    png = save_png(image)
    encoded = base64.b64encode(png).decode("ascii")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f'<image width="{size}" height="{size}" '
        f'href="data:image/png;base64,{encoded}"/></svg>\n'
    )
    return svg.encode("utf-8")


def contained_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit the source without cropping or adding a frame."""
    width, height = size
    scale = min(width / image.width, height / image.height)
    fitted_size = (round(image.width * scale), round(image.height * scale))
    fitted = resize(image, fitted_size)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas


def target_bytes(image: Image.Image, kind: str, dimensions: object) -> bytes:
    if kind == "svg":
        return svg_bytes(image, int(dimensions))
    if kind == "png":
        size = int(dimensions)
        return save_png(resize(image, (size, size)))
    if kind == "contain":
        return save_png(contained_image(image, dimensions))
    if kind == "wide":
        width, height = dimensions
        return save_png(contained_image(image, (width, height)))
    if kind == "ico":
        sizes = dimensions
        frames = [save_png(resize(image, (size, size))) for size in sizes]
        directory_size = 6 + 16 * len(sizes)
        entries = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
        offset = directory_size
        for size, frame in zip(sizes, frames):
            entries.extend(
                struct.pack(
                    "<BBBBHHII",
                    size if size < 256 else 0,
                    size if size < 256 else 0,
                    0,
                    0,
                    1,
                    32,
                    len(frame),
                    offset,
                )
            )
            offset += len(frame)
        return bytes(entries) + b"".join(frames)
    if kind == "icns":
        sizes = dimensions
        frames = [resize(image, (size, size)) for size in sizes]
        output = io.BytesIO()
        frames[0].save(output, format="ICNS", append_images=frames[1:])
        return output.getvalue()
    raise ValueError(f"unknown output kind: {kind}")


def describe_target(path: Path, kind: str, dimensions: object) -> str:
    if kind in ("png", "svg"):
        size = int(dimensions)
        return f"{kind.upper()} {size}x{size}"
    if kind in ("contain", "wide"):
        width, height = dimensions
        return f"PNG {width}x{height}"
    sizes = ", ".join(str(size) for size in dimensions)
    return f"{kind.upper()} [{sizes}]"


def cmd_write(source: Path, out: Path) -> int:
    try:
        image = load_source(source)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    failures = 0
    for relative, kind, dimensions in TARGETS:
        path = out / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(target_bytes(image, kind, dimensions))
            print(f"[write] {relative}: {describe_target(path, kind, dimensions)}")
        except (OSError, ValueError) as exc:
            failures += 1
            print(f"[error] {relative}: {exc}", file=sys.stderr)
    print(f"[write] generated {len(TARGETS) - failures}/{len(TARGETS)} icon assets")
    return int(failures > 0)


def cmd_check(source: Path, out: Path) -> int:
    try:
        image = load_source(source)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    failures = 0
    for relative, kind, dimensions in TARGETS:
        path = out / relative
        try:
            expected = target_bytes(image, kind, dimensions)
            if not path.is_file():
                raise FileNotFoundError("missing generated icon")
            if path.read_bytes() != expected:
                raise ValueError("stale; regenerate with node scripts/generate-icons.mjs")
            print(f"[ok] {relative}: {describe_target(path, kind, dimensions)}")
        except (OSError, ValueError) as exc:
            failures += 1
            print(f"[error] {relative}: {exc}", file=sys.stderr)
    print(f"[check] {len(TARGETS) - failures}/{len(TARGETS)} icon assets match the supplied source")
    return int(failures > 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    out = args.out or args.source
    return cmd_check(args.source, out) if args.check else cmd_write(args.source, out)


if __name__ == "__main__":
    raise SystemExit(main())
