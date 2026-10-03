import os

import pytest
from pulse_constants import set_pulse_home_override, reset_pulse_home_override

from pulse_cli.main_dashboard import _read_ssh_session_token_file


@pytest.mark.platforms("posix")  # POSIX fixture uses mode bits; Windows read_token requires protected DACLs
def test_token_file_is_read_and_unlinked_through_private_directory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    pulse_home = home / ".pulse"
    token_dir = pulse_home / "desktop-ssh" / ("a" * 32)
    token_dir.mkdir(parents=True, mode=0o700)
    token_path = token_dir / "0123456789abcdef.token"
    token_path.write_text("b" * 64)
    token_path.chmod(0o600)
    override = set_pulse_home_override(pulse_home)
    try:
        assert _read_ssh_session_token_file(str(token_path)) == "b" * 64
        assert not token_path.exists()
    finally:
        reset_pulse_home_override(override)


@pytest.mark.platforms("posix")  # POSIX desktop-ssh token path
def test_token_anchor_is_os_home_not_active_profile(tmp_path, monkeypatch):
    """Regression for #69551: the Desktop client always writes the token under
    ``$HOME/.pulse/desktop-ssh`` (a literal ``~/.pulse/desktop-ssh`` in
    apps/desktop/electron/remote-lifecycle.ts, expanded against the account's
    $HOME). A non-default sticky profile re-homes ``get_pulse_home()`` to
    ``<root>/profiles/<name>``, and a Docker-style ``PULSE_HOME`` can point
    elsewhere entirely — neither must move the validator off
    ``$HOME/.pulse/desktop-ssh``, or the token is wrongly rejected."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token_dir = home / ".pulse" / "desktop-ssh" / ("a" * 32)
    token_dir.mkdir(parents=True, mode=0o700)
    token_path = token_dir / "0123456789abcdef.token"

    # A sticky profile and a custom (Docker) root both point get_pulse_home()
    # away from $HOME/.pulse; the anchor must ignore both.
    for elsewhere in (home / ".pulse" / "profiles" / "coder", tmp_path / "opt" / "data"):
        token_path.write_text("b" * 64)
        token_path.chmod(0o600)
        override = set_pulse_home_override(elsewhere)
        try:
            assert _read_ssh_session_token_file(str(token_path)) == "b" * 64
            assert not token_path.exists()
        finally:
            reset_pulse_home_override(override)


@pytest.mark.platforms("posix")  # POSIX desktop-ssh token path
def test_token_under_profile_desktop_ssh_is_rejected(tmp_path, monkeypatch):
    """The client never writes under a profile-scoped desktop-ssh dir, so a token
    placed there must be rejected even while that profile is active — proving the
    anchor is the OS home, not the active profile (#69551)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    profile_home = home / ".pulse" / "profiles" / "coder"
    token_dir = profile_home / "desktop-ssh" / ("a" * 32)
    token_dir.mkdir(parents=True, mode=0o700)
    token_path = token_dir / "0123456789abcdef.token"
    token_path.write_text("b" * 64)
    token_path.chmod(0o600)
    override = set_pulse_home_override(profile_home)
    try:
        with pytest.raises(SystemExit, match="desktop-ssh directory"):
            _read_ssh_session_token_file(str(token_path))
    finally:
        reset_pulse_home_override(override)


@pytest.mark.platforms("posix")  # POSIX symlink contract
def test_token_file_rejects_symlink(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token_dir = home / ".pulse" / "desktop-ssh" / ("a" * 32)
    token_dir.mkdir(parents=True, mode=0o700)
    target = tmp_path / "token"
    target.write_text("b" * 64)
    target.chmod(0o600)
    token_path = token_dir / "0123456789abcdef.token"
    token_path.symlink_to(target)
    override = set_pulse_home_override(home / ".pulse")
    try:
        with pytest.raises(SystemExit, match="symlink|not accessible"):
            _read_ssh_session_token_file(str(token_path))
        assert not token_path.exists()
        assert target.read_text() == "b" * 64
    finally:
        reset_pulse_home_override(override)


@pytest.mark.platforms("linux")
def test_token_file_rejects_parent_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token_root = home / ".pulse" / "desktop-ssh"
    token_root.mkdir(parents=True, mode=0o700)
    escaped = token_root.parent / "0123456789abcdef.token"
    escaped.write_text("b" * 64)
    escaped.chmod(0o600)
    override = set_pulse_home_override(home / ".pulse")
    try:
        with pytest.raises(SystemExit, match="invalid runtime path"):
            _read_ssh_session_token_file(str(token_root / ".." / escaped.name))
        assert escaped.exists()
    finally:
        reset_pulse_home_override(override)


def test_windows_runtime_root_stays_at_machine_root_for_named_profile(tmp_path, monkeypatch):
    from pulse_cli import windows_ssh_runtime

    machine_root = tmp_path / "custom-pulse-root"
    monkeypatch.setenv("PULSE_HOME", str(machine_root / "profiles" / "writer_2"))

    assert windows_ssh_runtime._root() == machine_root / "desktop-ssh"
