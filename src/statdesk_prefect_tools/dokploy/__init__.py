"""Dokploy env-provisioning for the StatDeskAU prefect-worker (and similar
compose services).

Public surface:
  - `apply_env.main`          -- `statdesk-provision-worker-env` console script entrypoint
  - `manifest.Manifest`       -- Pydantic model for portfolio_env.yaml
  - `client.DokployClient`    -- thin REST wrapper around the Dokploy compose API
"""
from __future__ import annotations
