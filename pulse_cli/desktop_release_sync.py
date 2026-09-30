"""Sync the bundled desktop UI from our own GitHub Release channel.

Why this exists: the Electron UI bundle (app.asar + app.asar.unpacked) cannot
be rebuilt from this checkout (apps/desktop source is not vendored), so the
source-build path in source_build.py skips it. Without this module a `pulse
update` refreshes every Python product but leaves the desktop UI frozen at
whatever the last DMG install carried.

The channel is a rolling GitHub Release named ``desktop-ui`` on the SAME repo
this checkout clones from (derived from git remote, never hardcoded, never an
env var). The release holds:

    pulse-desktop-ui.tar.gz   app.asar + app.asar.unpacked/ (+ ui-manifest.json)
    ui-manifest.json          {"commit": "<payload HEAD>", "built_at": ...}

The installed app carries its UI version in
``Contents/Resources/ui-manifest.json`` (written by
scripts/build-pulse-app-dmg.sh). When the release manifest names a newer
commit, the bundle is downloaded (public URL, no auth), sha256-verified
against the manifest, and swapped into the installed app, which is then
re-signed ad-hoc on macOS.

Everything here is best-effort: any failure returns a skip reason string and
the update continues. stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

RELEASE_TAG = "desktop-ui"
UI_MANIFEST_NAME = "ui-manifest.json"
UI_BUNDLE_NAME = "pulse-desktop-ui.tar.gz"
NETWORK_TIMEOUT_SECONDS = 30


def _run_git(project_root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def release_owner_repo(project_root: Path) -> tuple[str, str] | None:
    """(owner, repo) from this checkout's origin, e.g. ('Anxious-Research', 'PULSE')."""
    url = _run_git(project_root, "remote", "get-url", "origin")
    if not url:
        return None
    url = url.strip().removesuffix(".git").removesuffix("/")
    for sep in ("github.com:", "github.com/"):
        if sep in url:
            path = url.split(sep, 1)[1]
            if "/" in path:
                owner, repo = path.split("/", 1)
                if owner and repo and "/" not in repo:
                    return owner, repo
    return None


def release_base_url(project_root: Path) -> str | None:
    ident = release_owner_repo(project_root)
    if ident is None:
        return None
    owner, repo = ident
    return f"https://github.com/{owner}/{repo}/releases/download/{RELEASE_TAG}"


def bundled_app_path() -> Path | None:
    """The installed desktop app, if this machine has one."""
    if sys.platform != "darwin":
        return None
    cand = Path("/Applications/Pulse.app")
    if cand.is_dir() and (cand / "Contents" / "Resources" / "app.asar").is_file():
        return cand
    return None


def installed_ui_commit(app: Path) -> str | None:
    try:
        manifest = json.loads((app / "Contents" / "Resources" / UI_MANIFEST_NAME).read_text())
        commit = manifest.get("commit")
        return commit if isinstance(commit, str) and commit else None
    except Exception:
        return None


def _fetch_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pulse-desktop-sync/1"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_file(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pulse-desktop-sync/1"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f, length=1024 * 256)
        return True
    except Exception:
        return False


def _swap_ui_bundle(app: Path, staged: Path) -> None:
    """Atomically replace app.asar + app.asar.unpacked/ inside the app bundle."""
    resources = app / "Contents" / "Resources"
    new_asar = staged / "app.asar"
    new_unpacked = staged / "app.asar.unpacked"
    new_manifest = staged / UI_MANIFEST_NAME
    if not new_asar.is_file() or not new_unpacked.is_dir():
        raise ValueError("release bundle missing app.asar or app.asar.unpacked/")
    backup_dir = resources / ".ui-sync-backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir()
    try:
        for name in ("app.asar", "app.asar.unpacked"):
            src = resources / name
            if src.exists() or src.is_symlink():
                src.rename(backup_dir / name)
        shutil.move(str(new_asar), str(resources / "app.asar"))
        shutil.move(str(new_unpacked), str(resources / "app.asar.unpacked"))
        if new_manifest.is_file():
            shutil.copy2(str(new_manifest), str(resources / UI_MANIFEST_NAME))
    except Exception:
        for name in ("app.asar", "app.asar.unpacked"):
            if not (resources / name).exists() and (backup_dir / name).exists():
                (backup_dir / name).rename(resources / name)
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)
    if sys.platform == "darwin":
        subprocess.run(
            ["codesign", "--deep", "--force", "--sign", "-", str(app)],
            capture_output=True, timeout=300,
        )


def sync_bundled_desktop_ui(project_root: Path) -> str:
    """Bring the installed app's UI bundle up to the release channel.

    Returns a one-line outcome for the update report; never raises.
    """
    try:
        return _sync_bundled_desktop_ui(project_root)
    except Exception as exc:
        return f"desktop UI release sync skipped ({exc})"


def _sync_bundled_desktop_ui(project_root: Path) -> str:
    app = bundled_app_path()
    if app is None:
        return "desktop UI release sync skipped (no bundled app installed)"
    base = release_base_url(project_root)
    if base is None:
        return "desktop UI release sync skipped (no GitHub origin)"
    manifest = _fetch_json(f"{base}/{UI_MANIFEST_NAME}")
    if not manifest or not manifest.get("commit"):
        return "desktop UI release sync skipped (no release published yet)"
    want = manifest["commit"]
    have = installed_ui_commit(app)
    if have == want:
        return f"desktop UI already current ({want[:12]})"
    digest = manifest.get("sha256", "")
    with tempfile.TemporaryDirectory(prefix="pulse-ui-sync-") as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / UI_BUNDLE_NAME
        if not _fetch_file(f"{base}/{UI_BUNDLE_NAME}", archive):
            return "desktop UI release sync skipped (download failed)"
        if digest:
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != digest:
                return "desktop UI release sync skipped (checksum mismatch)"
        staged = tmpdir / "staged"
        staged.mkdir()
        try:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(staged, filter="data")
        except Exception:
            return "desktop UI release sync skipped (bad archive)"
        try:
            _swap_ui_bundle(app, staged)
        except Exception as exc:
            return f"desktop UI release sync skipped (install failed: {exc})"
    try:
        from pulse_cli import update_receipt
        update_receipt.record_step("desktop_ui_release_sync", True, f"{have[:12] if have else 'none'} -> {want[:12]}")
    except Exception:
        pass
    return f"desktop UI updated to {want[:12]} (reopen the app to use it)"
