"""Every generated icon is a direct resize of the canonical PULSE artwork."""

import base64
import io
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

from PIL import Image

from scripts import generate_icons


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / generate_icons.SOURCE_IMAGE


def generate_to_temp(tmp_path: Path) -> Path:
    source = tmp_path / "source tree"
    (source / "assets").mkdir(parents=True)
    shutil.copy2(SOURCE, source / generate_icons.SOURCE_IMAGE)
    output = tmp_path / "generated"
    command = [
        sys.executable,
        str(ROOT / "scripts/generate_icons.py"),
        "--source",
        str(source),
        "--out",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    checked = subprocess.run(
        [*command, "--check"], capture_output=True, text=True, timeout=60
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    return output


def image_from_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_generated_icon_outputs_are_exact_resizes_of_supplied_image(tmp_path):
    output = generate_to_temp(tmp_path)
    with Image.open(SOURCE) as opened:
        original = opened.convert("RGBA")

    full_size_paths = (
        "apps/desktop/assets/icon.png",
        "apps/desktop/assets/icon-dark.png",
        "apps/desktop/public/apple-touch-icon.png",
        "apps/bootstrap-installer/src-tauri/icons/128x128@2x.png",
    )
    for relative in full_size_paths:
        size = Image.open(output / relative).size[0]
        rendered = Image.open(output / relative).convert("RGBA")
        expected = original.resize((size, size), Image.Resampling.LANCZOS)
        assert rendered.tobytes() == expected.tobytes(), relative

    _assert_container_frames(output, original)

    for relative in ("assets/icon-master.svg", "assets/icon-master-dark.svg", "website/static/img/favicon.svg"):
        root = ET.parse(output / relative).getroot()
        href = root.find("{http://www.w3.org/2000/svg}image").get("href")
        assert href.startswith("data:image/png;base64,"), relative
        embedded = image_from_bytes(base64.b64decode(href.split(",", 1)[1]))
        expected = original.resize(embedded.size, Image.Resampling.LANCZOS)
        assert embedded.tobytes() == expected.tobytes(), relative


def _assert_container_frames(output: Path, original: Image.Image) -> None:
    ico_sizes = {
        "apps/desktop/assets/icon.ico": {16, 24, 32, 48, 64, 128, 256},
        "apps/desktop/assets/icon-dark.ico": {16, 24, 32, 48, 64, 128, 256},
        "apps/bootstrap-installer/src-tauri/icons/icon.ico": {16, 32, 64, 128, 256},
        "website/static/img/favicon.ico": {16, 32, 48},
        "web/public/favicon.ico": {16, 32, 48},
    }
    for relative, expected_sizes in ico_sizes.items():
        path = output / relative
        data = path.read_bytes()
        count = struct.unpack_from("<H", data, 4)[0]
        sizes = {data[6 + index * 16] or 256 for index in range(count)}
        assert sizes == expected_sizes, relative
        for index in range(count):
            width, height, _, _, _, _, length, offset = struct.unpack_from(
                "<BBBBHHII", data, 6 + index * 16
            )
            size = width or 256
            assert (height or 256) == size, relative
            frame = Image.open(io.BytesIO(data[offset : offset + length])).convert("RGBA")
            expected = original.resize((size, size), Image.Resampling.LANCZOS)
            assert frame.tobytes() == expected.tobytes(), (relative, size)

    icns_paths = (
        "apps/desktop/assets/icon.icns",
        "apps/desktop/assets/icon-dark.icns",
        "apps/bootstrap-installer/src-tauri/icons/icon.icns",
    )
    for relative in icns_paths:
        container = Image.open(output / relative)
        sizes = container.info["sizes"]
        assert {width * scale for width, _, scale in sizes} == {32, 64, 128, 256, 512, 1024}
        for size in sizes:
            width, height, scale = size
            actual_size = (width * scale, height * scale)
            frame = container.icns.getimage(size).convert("RGBA")
            expected = original.resize(actual_size, Image.Resampling.LANCZOS)
            assert frame.tobytes() == expected.tobytes(), (relative, actual_size)
