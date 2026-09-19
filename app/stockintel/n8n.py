"""Outbound calls to the n8n webhooks that drive the pipeline.

Header auth only; the secret comes from the environment and never lives in workflow JSON.
"""

from __future__ import annotations

import logging

import requests

from .config import (
    HTTP_TIMEOUT,
    N8N_BACKFILL_WEBHOOK_PATH,
    N8N_BASE_URL,
    N8N_RUN_WEBHOOK_PATH,
    N8N_WEBHOOK_HEADER,
    N8N_WEBHOOK_SECRET,
)

log = logging.getLogger("stockintel.n8n")


class WebhookError(Exception):
    pass


def _post(path: str, payload: dict) -> dict:
    if not N8N_WEBHOOK_SECRET:
        raise WebhookError("STOCK_INTEL_N8N_WEBHOOK_SECRET not configured")
    url = f"{N8N_BASE_URL}/webhook/{path}"
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={N8N_WEBHOOK_HEADER: N8N_WEBHOOK_SECRET},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise WebhookError(f"n8n unreachable: {e}") from e

    if resp.status_code >= 400:
        raise WebhookError(f"n8n returned HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        return resp.json() or {}
    except ValueError:
        return {"raw": resp.text[:300]}


def fire_daily(as_of: str | None = None) -> dict:
    """Run-now. n8n answers immediately with the run_id it minted."""
    payload = {"source": "ui"}
    if as_of:
        payload["as_of"] = as_of
    log.info("firing daily webhook as_of=%s", as_of)
    return _post(N8N_RUN_WEBHOOK_PATH, payload)


def fire_backfill(tickers: list[str]) -> dict:
    """Two-year history backfill for newly added tickers."""
    log.info("firing backfill webhook tickers=%s", tickers)
    return _post(N8N_BACKFILL_WEBHOOK_PATH, {"tickers": tickers, "source": "ui"})
