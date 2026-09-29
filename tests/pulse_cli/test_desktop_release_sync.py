"""Behaviour contracts for the desktop UI release channel (pulse_cli.desktop_release_sync).

No network: HTTP is stubbed at the urllib seam; tarball/sha/swap run for real
against temp dirs. Covers: origin parsing, up-to-date no-op, missing channel,
bad checksum, and atomic swap with rollback.
Regression context: prebuilt desktop UI cannot be rebuilt from source, so
`pulse update` fetches it from the rolling desktop-ui release instead.
"""

import io
import json
import tarfile

import pulse_cli.desktop_release_sync as drs


def _git_remote(monkeypatch, url):
    monkeypatch.setattr(drs, "_run_git", lambda root, *a: url)


def test_owner_repo_parses_https_and_ssh(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://github.com/Anxious-Research/PULSE.git")
    assert drs.release_owner_repo(tmp_path) == ("Anxious-Research", "PULSE")
    _git_remote(monkeypatch, "git@github.com:Anxious-Research/PULSE.git")
    assert drs.release_owner_repo(tmp_path) == ("Anxious-Research", "PULSE")


def test_owner_repo_rejects_non_github(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://example.com/a/b.git")
    assert drs.release_owner_repo(tmp_path) is None
    _git_remote(monkeypatch, "")
    assert drs.release_owner_repo(tmp_path) is None


def test_release_base_url_shape(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://github.com/ACME/Widget.git")
    assert drs.release_base_url(tmp_path) == \
        "https://github.com/ACME/Widget/releases/download/desktop-ui"


UI_MANIFEST = "ui-manifest.json"


def _fake_app(tmp_path, commit):
    app = tmp_path / "Pulse.app"
    res = app / "Contents" / "Resources"
    res.mkdir(parents=True)
    (res / "app.asar").write_bytes(b"old-asar")
    (res / "app.asar.unpacked").mkdir()
    (res / UI_MANIFEST).write_text(json.dumps({"commit": commit}))
    return app


def test_up_to_date_is_noop(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://github.com/A/B.git")
    monkeypatch.setattr(drs, "bundled_app_path",
                        lambda: _fake_app(tmp_path, "abc123"))
    monkeypatch.setattr(drs, "_fetch_json",
                        lambda url: {"commit": "abc123", "sha256": "x"})
    out = drs.sync_bundled_desktop_ui(tmp_path)
    assert "already current" in out


def test_missing_release_skips(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://github.com/A/B.git")
    monkeypatch.setattr(drs, "bundled_app_path",
                        lambda: _fake_app(tmp_path, "abc123"))
    monkeypatch.setattr(drs, "_fetch_json", lambda url: None)
    out = drs.sync_bundled_desktop_ui(tmp_path)
    assert "skipped" in out and "no release" in out


def test_no_app_no_network(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(drs, "bundled_app_path", lambda: None)
    monkeypatch.setattr(drs, "_fetch_json",
                        lambda url: calls.append(url) or {"commit": "z"})
    out = drs.sync_bundled_desktop_ui(tmp_path)
    assert "no bundled app" in out and calls == []


def _bundle_bytes(commit):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("app.asar", b"new-asar"),
                           ("app.asar.unpacked/x", b"x"),
                           ("ui-manifest.json",
                            json.dumps({"commit": commit}).encode())):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_swap_installs_new_bundle_and_manifest(tmp_path, monkeypatch):
    import hashlib
    payload = _bundle_bytes("def456")
    digest = hashlib.sha256(payload).hexdigest()
    _git_remote(monkeypatch, "https://github.com/A/B.git")
    app = _fake_app(tmp_path, "abc123")
    monkeypatch.setattr(drs, "bundled_app_path", lambda: app)
    monkeypatch.setattr(drs, "_fetch_json",
                        lambda url: {"commit": "def456", "sha256": digest})
    monkeypatch.setattr(drs, "_fetch_file",
                        lambda url, dest: dest.write_bytes(payload) or True)
    monkeypatch.setattr(drs.sys, "platform", "linux")  # skip codesign
    out = drs.sync_bundled_desktop_ui(tmp_path)
    assert "updated to def456" in out
    assert (app / "Contents" / "Resources" / "app.asar").read_bytes() == b"new-asar"
    assert json.loads((app / "Contents" / "Resources" / UI_MANIFEST).read_text())["commit"] == "def456"


def test_checksum_mismatch_leaves_old_bundle(tmp_path, monkeypatch):
    _git_remote(monkeypatch, "https://github.com/A/B.git")
    app = _fake_app(tmp_path, "abc123")
    monkeypatch.setattr(drs, "bundled_app_path", lambda: app)
    monkeypatch.setattr(drs, "_fetch_json",
                        lambda url: {"commit": "def456", "sha256": "wrong"})
    monkeypatch.setattr(drs, "_fetch_file",
                        lambda url, dest: dest.write_bytes(_bundle_bytes("def456")) or True)
    out = drs.sync_bundled_desktop_ui(tmp_path)
    assert "checksum mismatch" in out
    assert (app / "Contents" / "Resources" / "app.asar").read_bytes() == b"old-asar"
