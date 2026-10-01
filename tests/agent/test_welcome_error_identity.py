"""The anonymous UI is a property of the request credential, not a free model or host (#111970)."""

from types import SimpleNamespace

import pytest

from agent.error_classifier import classify_api_error
from tests.pulse_cli.anon_portal import make_jwt
from agent.error_surface import build_error_surface_from_result
from agent.turn_recovery import max_retries_exhausted_result, nonretryable_client_error_result

WELCOME = "https://welcome-api.anxious-research.com/v1"
NAMED = "https://inference-api.anxious-research.com/v1"


def agent_for(api_key, base_url):
    return SimpleNamespace(
        provider="pulse", api_key=api_key, base_url=base_url, model="stepfun/step-3.7-flash:free",
        log_prefix="", _rate_limit_state=None, _has_pending_fallback=lambda: False,
        _dump_api_request_debug=lambda *a, **kw: None, _flush_status_buffer=lambda: None,
        _summarize_api_error=lambda e: str(e), _emit_status=lambda *a, **kw: None,
        _persist_session=lambda *a, **kw: None, _plines=lambda *a, **kw: None, _vprint=lambda *a, **kw: None,
        _buffer_status=lambda *a, **kw: None, _buffer_vprint=lambda *a, **kw: None,
        _emit_diagnostic_status=lambda *a, **kw: None, _buffer_diagnostic_status=lambda *a, **kw: None,
        _try_activate_fallback=lambda: False,
    )


@pytest.mark.parametrize("api_key", [make_jwt(account_tier="free", client_id="pulse-cli"), "sk-named"],
                         ids=["named-free", "api-key"])
@pytest.mark.parametrize("base_url", [NAMED, WELCOME])
@pytest.mark.parametrize("case", ["rate_limited", "model_not_free", "403"])
def test_named_errors_do_not_offer_anonymous_recovery(api_key, base_url, case):
    """A shared fairshare body or wrong welcome URL cannot establish anonymous identity."""
    status = 403 if case == "403" else 429
    message = ("You tried to access something that you don't have permissions for." if case == "403"
               else "The service refused this request.")
    body = {"status": status, "message": message, "reason": case, "retry_after": 158}
    error = Exception(message)
    error.status_code, error.body = status, body
    agent = agent_for(api_key, base_url)
    classified = classify_api_error(error, provider="pulse", model=agent.model, base_url=base_url, api_key=api_key)
    common = dict(api_kwargs=None, api_messages=[], messages=[], conversation_history=[],
                  api_call_count=1, approx_tokens=10, provider="pulse", base_url=base_url, model=agent.model)
    if status == 403:
        result = nonretryable_client_error_result(agent, error, classified, status_code=status, **common)
    else:
        result = max_retries_exhausted_result(agent, error, classified, max_retries=3,
                                             is_rate_limited=status == 429, error_msg=message, **common)
    assert "welcome_refusal" not in classified.error_context
    assert "welcome_route" not in classified.error_context
    assert "free_tier" not in result
    surface = build_error_surface_from_result(result, provider="pulse", model=agent.model)
    assert surface["code"] == {403: "auth", 429: "rate_limit"}[status]
    assert "without signing in" not in result["final_response"]


def guard_for(agent):
    from agent.turn_api_call import pulse_rate_limit_guard
    return pulse_rate_limit_guard(
        agent, _retry=None, api_messages=[], messages=[], conversation_history=[],
        active_system_prompt="system", retry_count=0, compression_attempts=0, api_call_count=0,
    )


def test_signing_in_does_not_inherit_anonymous_cooldown(tmp_path, monkeypatch):
    from agent.agent_runtime_helpers import extract_api_error_context
    from agent.pulse_rate_guard import clear_pulse_rate_limit, pulse_rate_limit_remaining, record_pulse_rate_limit
    from agent.turn_recovery import _is_genuine_pulse_rate_limit

    monkeypatch.setenv("PULSE_HOME", str(tmp_path))
    guest = agent_for(make_jwt(), WELCOME)
    error = Exception("refused")
    error.status_code = 429
    error.body = {"status": 429, "message": "refused", "reason": "rate_limited", "retry_after": 600}
    classified = classify_api_error(error, provider="pulse", base_url=WELCOME, api_key=guest.api_key)
    assert _is_genuine_pulse_rate_limit(guest, error, extract_api_error_context(error), classified)
    blocked = guard_for(guest)
    assert blocked.action == "return"
    assert build_error_surface_from_result(blocked.result)["code"] == "free_tier_rate_limited"

    # Keep the old welcome URL deliberately: the changed credential owns the boundary.
    guest.api_key = make_jwt(account_tier="free", client_id="pulse-cli")
    assert guard_for(guest).action == "fallthrough"
    record_pulse_rate_limit(headers={"retry-after": "300"})
    named_blocked = guard_for(guest)
    assert named_blocked.action == "return"
    assert "free_tier" not in named_blocked.result
    clear_pulse_rate_limit()
    assert pulse_rate_limit_remaining() is None
    assert pulse_rate_limit_remaining(anonymous=True) > 0


def test_auxiliary_anonymous_cooldown_does_not_outlive_signing_in(tmp_path, monkeypatch):
    """An anonymous cooldown marks the provider unhealthy only briefly: a named sign-in
    mid-cooldown must not inherit the anonymous allowance's wait."""
    import agent.auxiliary_client as aux
    from agent.pulse_rate_guard import record_pulse_rate_limit

    monkeypatch.setenv("PULSE_HOME", str(tmp_path))
    record_pulse_rate_limit(headers={"retry-after": "600"}, anonymous=True)
    runtime = [make_jwt(), WELCOME]
    monkeypatch.setattr(aux, "_read_pulse_auth", lambda: {})
    monkeypatch.setattr(aux, "_resolve_pulse_runtime_api", lambda **kw: tuple(runtime))
    unhealthy = []
    monkeypatch.setattr(aux, "_mark_provider_unhealthy", lambda *a, **kw: unhealthy.append(kw.get("ttl")))
    client = object()
    monkeypatch.setattr(aux, "_create_openai_client", lambda **kw: client)
    monkeypatch.setattr(aux, "_aux_probe_active", lambda: True)
    assert aux._try_pulse() == (None, None)
    assert unhealthy and all(ttl <= 60 for ttl in unhealthy)
    runtime[:] = [make_jwt(account_tier="free", client_id="pulse-cli"), NAMED]
    assert aux._try_pulse()[0] is client


@pytest.mark.parametrize("tier", ["anonymous", "free"])
def test_401_diagnostics_follow_request_identity_on_welcome_host(tier, capsys, monkeypatch):
    import agent.conversation_loop as loop
    from agent.turn_recovery import _print_pulse_401_diagnostics

    monkeypatch.setattr(loop, "_print_pulse_entitlement_guidance", lambda *a: False)
    _print_pulse_401_diagnostics(agent_for(make_jwt(account_tier=tier), WELCOME), Exception("unauthorized"))
    output = capsys.readouterr().out
    assert ("PULSE couldn't start a new one" in output) == (tier == "anonymous")
    assert ("pulse auth add pulse" in output) == (tier != "anonymous")


def test_anonymous_claim_does_not_classify_other_providers_as_pulse():
    error = Exception("refused")
    error.status_code = 429
    error.body = {"reason": "rate_limited", "retry_after": 600}
    classified = classify_api_error(error, provider="custom", api_key=make_jwt(), base_url=WELCOME)
    assert "welcome_refusal" not in classified.error_context


def test_named_account_on_welcome_host_gets_reconnect_copy_without_signin_card():
    """The gateway's mirror 400 keeps its reconnect copy for a signed-in user; the sign-in card would
    ask for a sign-in that already happened."""
    message = "This endpoint serves anonymous PULSE Agent accounts only. Use https://inference-api.anxious-research.com with your API key or signed-in account."
    error = Exception(message)
    error.status_code, error.body = 400, {"status": 400, "message": message}
    agent = agent_for(make_jwt(account_tier="free", client_id="pulse-cli"), WELCOME)
    classified = classify_api_error(error, provider="pulse", model=agent.model, base_url=WELCOME, api_key=agent.api_key)
    result = nonretryable_client_error_result(
        agent, error, classified, status_code=400, api_kwargs=None, api_messages=[], messages=[],
        conversation_history=[], api_call_count=1, approx_tokens=10, provider="pulse", base_url=WELCOME, model=agent.model)
    assert "needs to reconnect" in result["final_response"]
    assert "free_tier" not in result
    assert "sign in" not in result["final_response"].lower()


def test_escaped_exception_surface_keeps_the_anonymous_verdict():
    """The desktop's escaped-exception card classifies with the request credential too."""
    from agent.error_surface import build_error_surface_from_exception
    error = Exception("refused")
    error.status_code = 429
    error.body = {"status": 429, "message": "refused", "reason": "model_not_free", "alternates": ["pulse/welcome"]}
    anonymous = build_error_surface_from_exception(error, provider="pulse", model="gpt-5", api_key=make_jwt())
    named = build_error_surface_from_exception(error, provider="pulse", model="gpt-5", api_key=make_jwt(account_tier="free"))
    assert anonymous["retryable"] is False
    assert named["retryable"] is True
