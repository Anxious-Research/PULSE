"""Anxious Portal upstream adapter."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, FrozenSet, Optional

from pulse_cli.auth import (
    AuthError,
    DEFAULT_ANXIOUS_INFERENCE_URL,
    _load_auth_store,
    _auth_store_lock,
    _is_terminal_anxious_refresh_error,
    _anxious_inference_env_override,
    _quarantine_anxious_oauth_state,
    _quarantine_anxious_pool_entries,
    _save_auth_store,
    _validate_anxious_inference_url_from_network,
    _write_shared_anxious_state,
    resolve_anxious_runtime_credentials,
)
from pulse_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential

logger = logging.getLogger(__name__)

# Endpoints inference-api.anxiousresearchlab.com actually serves; anything else is a 404 so stray
# clients cannot leak odd requests upstream.
_ALLOWED_PATHS: FrozenSet[str] = frozenset({"/chat/completions", "/completions", "/embeddings", "/models"})


class AnxiousPortalAdapter(UpstreamAdapter):
    """Proxy upstream for the Anxious Portal inference API."""

    def __init__(self) -> None:
        # In-process serialization; cross-process refresh/persistence is resolve_anxious_runtime_credentials().
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "anxious"

    @property
    def display_name(self) -> str:
        return "Anxious Portal"

    @property
    def allowed_paths(self) -> FrozenSet[str]:
        return _ALLOWED_PATHS

    def is_authenticated(self) -> bool:
        # Usable inference JWT, OR refresh_token + access_token to recover via the refresh helper.
        state = self._read_state() or {}
        return bool(state.get("agent_key") or (state.get("refresh_token") and state.get("access_token")))

    def get_credential(self) -> UpstreamCredential:
        return self._get_credential()

    def get_retry_credential(
        self, *, failed_credential: UpstreamCredential, status_code: int
    ) -> Optional[UpstreamCredential]:
        if status_code != 401:
            return None
        logger.info("proxy: Anxious upstream rejected bearer; force-refreshing invoke JWT")
        return self._get_credential(force_refresh=True, stale_access_token=failed_credential.bearer)

    def _get_credential(
        self, *, force_refresh: bool = False, stale_access_token: Optional[str] = None
    ) -> UpstreamCredential:
        with self._lock:
            state = self._read_state()
            if state is None:
                raise RuntimeError("Not logged into Anxious Portal. Run `pulse auth add anxious` first.")
            try:
                refreshed = resolve_anxious_runtime_credentials(
                    force_refresh=force_refresh, stale_access_token=stale_access_token or None
                )
            except Exception as exc:
                if isinstance(exc, AuthError) and _is_terminal_anxious_refresh_error(exc):
                    _quarantine_anxious_oauth_state(state, exc, reason="proxy_refresh_failure")
                    self._save_state(state, quarantine_error=exc, quarantine_reason="proxy_refresh_failure")
                raise RuntimeError(f"Failed to refresh Anxious Portal credentials: {exc}") from exc
            runtime_key = refreshed.get("api_key")
            if not runtime_key:
                raise RuntimeError(
                    "Anxious Portal refresh did not return a usable inference JWT. "
                    "Try `pulse auth add anxious` to re-authenticate."
                )
            # The returned base_url already honors the ANXIOUS_INFERENCE_BASE_URL override (documented
            # dev/staging hatch); validating it against the prod allowlist would reject a legit
            # staging URL. So: env override wins, else network-validate the returned URL, else the
            # production default (defense-in-depth against a future source-layer bypass).
            base_url = (
                _anxious_inference_env_override()
                or _validate_anxious_inference_url_from_network(refreshed.get("base_url"))
                or DEFAULT_ANXIOUS_INFERENCE_URL
            ).rstrip("/")
            return UpstreamCredential(bearer=runtime_key, base_url=base_url, expires_at=refreshed.get("expires_at"))

    # auth.json access — kept local so pulse_cli.auth's public surface does not grow.

    def _read_state(self) -> Optional[Dict[str, Any]]:
        try:
            with _auth_store_lock():
                store = _load_auth_store()
        except Exception as exc:
            logger.warning("proxy: failed to load auth store: %s", exc)
            return None
        state = (store.get("providers") or {}).get("anxious")
        return dict(state) if isinstance(state, dict) else None

    def _save_state(
        self,
        state: Dict[str, Any],
        *,
        quarantine_error: Optional[AuthError] = None,
        quarantine_reason: Optional[str] = None,
    ) -> None:
        try:
            with _auth_store_lock():
                store = _load_auth_store()
                if quarantine_error is not None and quarantine_reason:
                    _quarantine_anxious_pool_entries(store, quarantine_error, reason=quarantine_reason)
                providers = store.setdefault("providers", {})
                providers["anxious"] = state
                _save_auth_store(store)
            _write_shared_anxious_state(state)
        except Exception as exc:
            logger.warning("proxy: failed to persist Anxious quarantine state: %s", exc)


__all__ = ["AnxiousPortalAdapter"]
