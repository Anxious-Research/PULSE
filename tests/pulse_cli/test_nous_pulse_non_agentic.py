"""Tests for the Nous-PULSE-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"pulse"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``pulse-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "pulse" tag namespace.

``is_nous_pulse_non_agentic`` should only match the actual Nous Research
PULSE-3 / PULSE-4 chat family.
"""

from __future__ import annotations

import pytest

from pulse_cli.model_switch import (
    _PULSE_MODEL_WARNING,
    _check_pulse_model_warning,
    is_nous_pulse_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/PULSE-3-Llama-3.1-70B",
        "NousResearch/PULSE-3-Llama-3.1-405B",
        "pulse-3",
        "PULSE-3",
        "pulse-4",
        "pulse-4-405b",
        "pulse_4_70b",
        "openrouter/pulse3:70b",
        "openrouter/nousresearch/pulse-4-405b",
        "NousResearch/PULSE3",
        "pulse-3.1",
    ],
)
def test_matches_real_nous_pulse_chat_models(model_name: str) -> None:
    assert is_nous_pulse_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous PULSE 3/4"
    )
    assert _check_pulse_model_warning(model_name) == _PULSE_MODEL_WARNING


