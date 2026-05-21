"""Pydantic schema for `portfolio_env.yaml`.

The manifest is keys-and-metadata only -- values stay in the operator's
secrets manager / shell env / Dokploy and are resolved at apply-time.
Committing values would defeat the entire point of having a secrets manager.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class EnvKey(BaseModel):
    """One env-var entry in the manifest."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """The env-var name as it appears in the Dokploy service (e.g. `PGHOST`)."""

    required: bool = True
    """If True and not present in the caller's env at apply-time, fail fast."""

    sensitive: bool = False
    """If True, mask the value in diff output. Doesn't change wire format."""

    dollar_escape: bool = False
    """If True, replace `$` with `$$` before pushing to Dokploy.

    Docker Compose's `.env` file interpolation eats unescaped `$X` sequences,
    truncating passwords containing `$`. The canonical workaround is to
    double the dollar signs in the value Dokploy stores. Only set True for
    raw secrets known to contain `$`; base64-encoded fallbacks (e.g.
    `SUPABASE_PG_PASSWORD_B64`) don't need it because base64 has no `$`.
    """

    comment: str | None = None
    """Free-form note describing what this env var is for. Operator-facing only."""


class Service(BaseModel):
    """Which Dokploy service to provision."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["compose"] = "compose"
    """Currently only `compose` services are supported. Standalone applications
    use a different endpoint shape (`application.update`) -- add later if needed."""

    compose_id: str | None = None
    """Dokploy composeId. Optional if `compose_id_env` is given."""

    compose_id_env: str | None = None
    """Env var that holds the composeId. Use this when the value should not
    be committed to the manifest (e.g. you don't want anyone guessing service
    IDs from a public manifest)."""

    api_base: str = "https://admin.statdesk.com.au/api"
    """Dokploy API base URL."""


class Manifest(BaseModel):
    """Top-level manifest for one Dokploy service."""

    model_config = ConfigDict(extra="forbid")

    service: Service
    env: list[EnvKey] = Field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    """Parse a YAML manifest from disk."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Manifest.model_validate(raw)


__all__ = ["EnvKey", "Service", "Manifest", "load_manifest"]
