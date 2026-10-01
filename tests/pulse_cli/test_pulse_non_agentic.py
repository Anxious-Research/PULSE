"""Tests for the PULSE-PULSE-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"pulse"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``pulse-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "pulse" tag namespace.

``is_pulse_non_agentic`` should only match the actual Anxious Research
PULSE-3 / PULSE-4 chat family.
"""

from __future__ import annotations

import pytest

from pulse_cli.model_switch import (
    _PULSE_MODEL_WARNING,
    _check_pulse_model_warning,
    is_pulse_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "AnxiousResearch/PULSE-3-Llama-3.1-70B",
        "AnxiousResearch/PULSE-3-Llama-3.1-405B",
        "pulse-3",
        "PULSE-3",
        "pulse-4",
        "pulse-4-405b",
        "pulse_4_70b",
        "openrouter/pulse3:70b",
        "openrouter/anxious-research/pulse-4-405b",
        "AnxiousResearch/PULSE3",
        "pulse-3.1",
    ],
)
def test_matches_real_pulse_pulse_chat_models(model_name: str) -> None:
    assert is_pulse_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as PULSE PULSE 3/4"
    )
    assert _check_pulse_model_warning(model_name) == _PULSE_MODEL_WARNING


