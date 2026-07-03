"""ZetaVPN panel — FastAPI application entrypoint.

Serves the REST API under ``{base}/api``, the public subscription endpoints under
``{base}/sub`` and the self-contained web UI portal from ``frontend/``.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


def _page(name: str) -> FileResponse | JSONResponse:
    path = FRONTEND / name
    if not path.is_file():
        return JSONResponse({"detail": f"{name} not found — frontend not installed"}, status_code=404)
    return FileResponse(str(path))


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
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    run()
