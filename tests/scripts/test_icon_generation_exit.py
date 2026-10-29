"""Icon generation validates its source and reports missing or stale outputs."""

from pathlib import Path
import shutil

from PIL import Image

from scripts import generate_icons


ROOT = Path(__file__).resolve().parents[2]


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source tree"
    (source / "assets").mkdir(parents=True)
    shutil.copy2(ROOT / generate_icons.SOURCE_IMAGE, source / generate_icons.SOURCE_IMAGE)
    return source


def test_write_and_check_report_all_targets(tmp_path, capsys):
    source = make_source(tmp_path)
    output = tmp_path / "output tree"

    assert generate_icons.cmd_write(source, output) == 0
    written = capsys.readouterr().out
    assert f"generated {len(generate_icons.TARGETS)}/{len(generate_icons.TARGETS)}" in written
    assert generate_icons.cmd_check(source, output) == 0
    checked = capsys.readouterr().out
    assert f"{len(generate_icons.TARGETS)}/{len(generate_icons.TARGETS)} icon assets match" in checked


def test_check_fails_for_missing_or_stale_assets(tmp_path, capsys):
    source = make_source(tmp_path)
    output = tmp_path / "output tree"
    assert generate_icons.cmd_write(source, output) == 0
    capsys.readouterr()

    (output / generate_icons.TARGETS[0][0]).unlink()
    stale = output / generate_icons.TARGETS[2][0]
    stale.write_bytes(b"not the generated icon")
    assert generate_icons.cmd_check(source, output) == 1
    errors = capsys.readouterr().err
    assert "missing generated icon" in errors
    assert "stale; regenerate" in errors


def test_source_must_be_square_and_existing_assets_are_not_modified(tmp_path, capsys):
    source = make_source(tmp_path)
    path = source / generate_icons.SOURCE_IMAGE
    Image.new("RGBA", (8, 6), (0, 0, 0, 255)).save(path)
    output = tmp_path / "output tree"
    assert generate_icons.cmd_write(source, output) == 1
    assert not output.exists()
    assert "expected a square source image" in capsys.readouterr().err
