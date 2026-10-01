from pathlib import Path

import pytest

from agent import account_usage, billing_usage
from pulse_constants import (
    get_pulse_home,
    reset_pulse_home_override,
    set_pulse_home_override,
)


@pytest.mark.parametrize(
    "fetcher",
    [account_usage._fetch_portal_account, billing_usage.fetch_pulse_account],
)
def test_pulse_account_fetch_preserves_profile_home_in_timeout_worker(
    monkeypatch, tmp_path: Path, fetcher
):
    """The bounded account fetch must read auth from the routed profile, not the launch profile."""
    launch_home = tmp_path / "launch"
    profile_home = tmp_path / "profiles" / "secondary"
    launch_home.mkdir()
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("PULSE_HOME", str(launch_home))

    observed_homes: list[Path] = []

    def fake_account_fetch(*, force_fresh: bool = False):
        assert force_fresh is True
        observed_homes.append(get_pulse_home())
        return observed_homes[-1]

    monkeypatch.setattr(
        "pulse_cli.pulse_account.get_pulse_portal_account_info",
        fake_account_fetch,
    )

    token = set_pulse_home_override(profile_home)
    try:
        assert get_pulse_home() == profile_home
        assert fetcher(1.0) == profile_home
    finally:
        reset_pulse_home_override(token)

    assert observed_homes == [profile_home]
