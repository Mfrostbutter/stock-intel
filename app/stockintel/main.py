"""stock-intel - FastAPI service for the Stock Intel brief, watchlist and analyst.

Self-hosted and single-tenant: bearer token on every /api route, loopback bind by default.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from . import api, db
from .analyst import service as analyst_service
from .auth import require_bearer, require_bearer_or_n8n_secret
from .config import APP_TOKEN, STATIC_DIR, VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("stockintel")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        db.open_pool()
        analyst_service.reap_orphans()
    except Exception as e:  # noqa: BLE001 - a dead DB must not stop /health from answering
        log.error("Postgres pool did not open: %s", e)
    if not APP_TOKEN:
        log.error("STOCK_INTEL_APP_TOKEN is empty; every /api route will answer 503.")
    yield
    db.close_pool()


app = FastAPI(title="stock-intel", version=VERSION, lifespan=lifespan)

app.include_router(api.router, dependencies=[Depends(require_bearer)])
app.include_router(api.hook_router, dependencies=[Depends(require_bearer_or_n8n_secret)])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "stock-intel", "version": VERSION, "db": db.ping()}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    """Single-file SPA. The page loads unauthenticated so the token can be pasted;
    every /api call it makes is bearer-checked server-side."""
    html_path = STATIC_DIR / "ui.html"
    if not html_path.is_file():
        raise HTTPException(status_code=500, detail="ui.html not found")
    return FileResponse(str(html_path), media_type="text/html")


@app.get("/m", include_in_schema=False)
def mobile_ui() -> FileResponse:
    """Phone-first companion page. Same token, same API, five tabs."""
    html_path = STATIC_DIR / "mobile.html"
    if not html_path.is_file():
        raise HTTPException(status_code=500, detail="mobile.html not found")
    return FileResponse(str(html_path), media_type="text/html")
