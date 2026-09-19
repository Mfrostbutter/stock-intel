"""Bearer auth. Refuses to serve at all when no token is configured."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from .config import APP_TOKEN, N8N_WEBHOOK_SECRET


def require_bearer(authorization: Annotated[str | None, Header()] = None) -> None:
    if not APP_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STOCK_INTEL_APP_TOKEN not configured; service is locked down.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    presented = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(presented, APP_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


def require_bearer_or_n8n_secret(
    authorization: Annotated[str | None, Header()] = None,
    x_stock_intel_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Routes n8n calls at the end of a run: the shared webhook secret is accepted instead of the bearer."""
    if x_stock_intel_secret and N8N_WEBHOOK_SECRET and hmac.compare_digest(x_stock_intel_secret, N8N_WEBHOOK_SECRET):
        return
    require_bearer(authorization)
