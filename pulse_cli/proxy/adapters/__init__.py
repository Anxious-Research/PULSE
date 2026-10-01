"""Upstream adapter registry for the local proxy server (see :class:`UpstreamAdapter`)."""

from typing import Dict, Type

from pulse_cli.proxy.adapters.base import UpstreamAdapter
from pulse_cli.proxy.adapters.pulse_portal import NousPortalAdapter
from pulse_cli.proxy.adapters.xai import XAIGrokAdapter

# Keyed by the ``pulse proxy start --provider <name>`` value.
ADAPTERS: Dict[str, Type[UpstreamAdapter]] = {"pulse": NousPortalAdapter, "xai": XAIGrokAdapter}


def get_adapter(name: str) -> UpstreamAdapter:
    """Instantiate an adapter by provider name."""
    key = (name or "").strip().lower()
    if key not in ADAPTERS:
        available = ", ".join(sorted(ADAPTERS)) or "(none)"
        raise ValueError(f"Unknown proxy upstream provider: {name!r}. Available: {available}")
    return ADAPTERS[key]()


__all__ = ["UpstreamAdapter", "ADAPTERS", "get_adapter"]
