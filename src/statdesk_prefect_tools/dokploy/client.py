"""Thin REST wrapper for the Dokploy compose API.

Two endpoints used by the env-provisioning flow:
  - GET  /compose.one?composeId=...   -> current compose state (incl. env)
  - POST /compose.update              -> update compose (full replacement of env)

API auth: `x-api-key: <DOKPLOY_API_KEY>`.

Reference: Calendar_Pipeline `docs/OPERATIONS.md`.
"""
from __future__ import annotations

import os
from typing import Any

import requests


class DokployError(RuntimeError):
    """Raised on non-2xx responses or network failures."""


class DokployClient:
    """Stateless client. Construct once per CLI invocation."""

    def __init__(
        self,
        api_base: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key or os.environ.get("DOKPLOY_API_KEY", "")
        if not self.api_key:
            raise DokployError(
                "Dokploy API key missing. Set DOKPLOY_API_KEY env var "
                "(or pass api_key=... to DokployClient)."
            )
        self.timeout = timeout

    # --- low-level ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DokployError(f"GET {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise DokployError(
                f"GET {url} returned {resp.status_code}: {resp.text[:500]}"
            )
        return resp.json()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DokployError(f"POST {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise DokployError(
                f"POST {url} returned {resp.status_code}: {resp.text[:500]}"
            )
        return resp.json() if resp.text else {}

    # --- compose endpoints --------------------------------------------------

    def get_compose(self, compose_id: str) -> dict[str, Any]:
        """Return the full compose object, including the `env` field."""
        return self._get("compose.one", composeId=compose_id)

    def get_compose_env(self, compose_id: str) -> dict[str, str]:
        """Return current env as a dict.

        Dokploy stores env as a single newline-delimited string in the
        compose object's `env` field. Lines beginning with `#` are comments.
        We preserve order in the returned dict for stable diffing.
        """
        compose = self.get_compose(compose_id)
        raw = compose.get("env") or ""
        return parse_env_block(raw)

    def update_compose_env(
        self,
        compose_id: str,
        env: dict[str, str],
        *,
        title: str = "statdesk-provision-worker-env",
        description: str | None = None,
    ) -> dict[str, Any]:
        """Replace the compose service's env block.

        Dokploy's compose.update is a FULL replacement of the env field --
        pass the complete merged dict, not just the deltas. Caller is
        expected to have read get_compose_env first, merged in changes,
        and now wants to push the result.
        """
        body: dict[str, Any] = {
            "composeId": compose_id,
            "env": render_env_block(env),
        }
        # `title` / `description` are deployment-history annotations on
        # `compose.deploy`, NOT on `compose.update`. Keep the params here
        # so the CLI signature is consistent, but they're not sent.
        del title, description
        return self._post("compose.update", body)

    def deploy_compose(
        self,
        compose_id: str,
        *,
        title: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Trigger a redeploy so env changes propagate to the running container."""
        body: dict[str, Any] = {
            "composeId": compose_id,
            "title": title,
        }
        if description:
            body["description"] = description
        return self._post("compose.deploy", body)


# ---------------------------------------------------------------------------
# Helpers (kept pure so tests can exercise them without HTTP)
# ---------------------------------------------------------------------------


def parse_env_block(raw: str) -> dict[str, str]:
    """Parse a Dokploy env block (newline-delimited KEY=VALUE) into a dict.

    Preserves insertion order. Skips comment lines and blanks. Trims leading
    and trailing whitespace from keys; preserves the value verbatim (callers
    may need to round-trip `$$` -> `$` themselves).
    """
    out: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value
    return out


def render_env_block(env: dict[str, str]) -> str:
    """Render a dict back to a Dokploy env block.

    One `KEY=VALUE` per line. No quoting -- Dokploy's parser treats the
    text verbatim. Keys are emitted in dict insertion order (Python 3.7+
    guarantees this), so callers can control ordering by building the
    dict carefully.
    """
    return "\n".join(f"{k}={v}" for k, v in env.items())


__all__ = ["DokployClient", "DokployError", "parse_env_block", "render_env_block"]
