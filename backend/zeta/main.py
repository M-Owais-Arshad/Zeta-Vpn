"""ZetaVPN panel — FastAPI application entrypoint.

ZetaVPN by Muhammad Owais · © 2026 · AGPL-3.0.

Serves the REST API under ``{base}/api``, the public subscription endpoints under
``{base}/sub`` and the self-contained web UI portal from ``frontend/``.
"""

from __future__ import annotations

import contextlib
import logging
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router, sub_router
from .api.settings import load_into_settings
from .bootstrap import ensure_admin, seed_settings
from .config import settings
from .db import SessionLocal, init_db
from .tasks import stats_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zeta")

BASE = settings.base_path  # "" or "/xxxx"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    init_db()
    db = SessionLocal()
    try:
        ensure_admin(db)
        seed_settings(db)
        load_into_settings(db)
    finally:
        db.close()

    poller = asyncio.create_task(stats_loop())
    log.info("%s panel v%s ready on %s:%s%s", settings.brand, __version__, settings.host, settings.port, BASE or "/")
    try:
        yield
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller


app = FastAPI(
    title=f"{settings.brand} Panel",
    version=__version__,
    docs_url=f"{BASE}/api/docs",
    openapi_url=f"{BASE}/api/openapi.json",
    lifespan=lifespan,
)

# Only enable CORS when explicitly configured. The UI is same-origin and auth is
# Bearer-token based, so cross-origin access is off by default (see config.py).
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-XSS-Protection", "0")
    return response


# --- API + subscription ------------------------------------------------------
app.include_router(api_router, prefix=f"{BASE}/api")
app.include_router(sub_router, prefix=BASE)


@app.get(f"{BASE}/api/health")
def health() -> dict:
    return {"status": "ok", "brand": settings.brand, "version": __version__}


# --- Static UI ---------------------------------------------------------------
FRONTEND = settings.frontend_dir
ASSETS = FRONTEND / "assets"
if ASSETS.is_dir():
    app.mount(f"{BASE}/assets", StaticFiles(directory=str(ASSETS)), name="assets")


_ASSET_FILES = ("js/app.js", "js/api.js", "css/zeta.css")


def _asset_version() -> str:
    """Cache-busting token that changes whenever a frontend asset changes.

    index.html hardcodes ``?v=<x.y.z>`` on its <script>/<link> tags; if that
    string never changes, a panel update leaves the browser (and any CDN) serving
    a STALE app.js under the assets' 4h max-age — the "new feature/button missing
    after update" symptom. Deriving the token from the assets' newest mtime busts
    the cache automatically on every deploy."""
    try:
        latest = max(
            (ASSETS / f).stat().st_mtime for f in _ASSET_FILES if (ASSETS / f).is_file()
        )
        return f"{settings.version}-{int(latest)}"
    except (OSError, ValueError):
        return settings.version


def _page(name: str) -> HTMLResponse | JSONResponse:
    path = FRONTEND / name
    if not path.is_file():
        return JSONResponse({"detail": f"{name} not found — frontend not installed"}, status_code=404)
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return JSONResponse({"detail": f"{name} unreadable"}, status_code=500)
    # Rewrite each asset's ?v=... to the per-deploy token so a stale app.js/CSS
    # can't survive an update. index.html itself is returned uncached (HTMLResponse
    # sets no max-age), so the fresh token is always seen.
    html = re.sub(r"(\.(?:js|css))\?v=[0-9A-Za-z.\-]+", rf"\1?v={_asset_version()}", html)
    return HTMLResponse(html)


@app.get(f"{BASE}/", include_in_schema=False)
@app.get(f"{BASE}/login", include_in_schema=False)
def index():
    return _page("index.html")


@app.get(f"{BASE}/portal", include_in_schema=False)
def portal():
    return _page("sub.html")


def run() -> None:
    """Console entrypoint used by the systemd unit."""
    import uvicorn

    uvicorn.run(
        "zeta.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_level="info",
    )


if __name__ == "__main__":
    run()
