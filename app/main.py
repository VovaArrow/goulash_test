"""Точка входа: `uvicorn app.main:app`."""
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import db
from .api import Store, router, get_conn
from .collections import build_router
from .seed import seed_db

STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app(db_path: str | None = None, *, public_demo: bool | None = None) -> FastAPI:
    path = db_path or os.environ.get("GOULASH_DB", "data/goulash.db")

    public = public_demo if public_demo is not None else os.environ.get("GOULASH_PUBLIC_DEMO") == "1"
    public_origin = os.environ.get("GOULASH_PUBLIC_ORIGIN", "").rstrip("/") if public else ""
    if public_origin:
        origin_parts = urlsplit(public_origin)
        if (origin_parts.scheme not in ("https", "http") or not origin_parts.netloc
                or origin_parts.path or origin_parts.query or origin_parts.fragment
                or origin_parts.username or origin_parts.password):
            raise ValueError("GOULASH_PUBLIC_ORIGIN должен содержать только протокол и домен")
    sessions = Path(os.environ.get("GOULASH_SESSIONS_DIR", str(Path(path).parent / "sessions")))
    session_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(path)
        db.init_db(conn)
        if db.is_empty(conn):
            seed_db(conn)
        conn.close()
        app.state.store = Store(path)
        if public:
            sessions.mkdir(parents=True, exist_ok=True)
            template = sessions / "template.db"
            conn = db.connect(str(template))
            db.init_db(conn)
            seed_db(conn)
            conn.close()
            # Inactive demo sessions expire after seven days, including SQLite sidecars.
            for candidate in sessions.glob("*.db"):
                if candidate.name != "template.db" and candidate.stat().st_mtime < time.time() - 7 * 86400:
                    for suffix in ("", "-wal", "-shm"):
                        Path(str(candidate) + suffix).unlink(missing_ok=True)
        yield

    app = FastAPI(title="Goulash · Поставщики", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    @app.middleware("http")
    async def demo_session(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected_origin = public_origin or str(request.base_url).rstrip("/")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected_origin):
                return JSONResponse({"detail": "Запрос с другого сайта запрещён"}, status_code=403)
        token = None
        if public and (request.url.path == "/" or request.url.path.startswith("/api/")):
            supplied = request.cookies.get("goulash_session", "")
            with session_lock:
                token = supplied if re.fullmatch(r"[a-f0-9]{64}", supplied) and (sessions / f"{supplied}.db").exists() else secrets.token_hex(32)
                session_path = sessions / f"{token}.db"
                if not session_path.exists():
                    # SQLite backup includes committed WAL data and keeps session copies consistent.
                    source = db.connect(str(sessions / "template.db"))
                    target = db.connect(str(session_path))
                    try:
                        source.backup(target)
                    finally:
                        source.close()
                        target.close()
            request.state.db_path = str(session_path)
        response = await call_next(request)
        if token:
            response.set_cookie("goulash_session", token, httponly=True, samesite="lax",
                                secure=public_origin.startswith("https://") or request.url.scheme == "https", max_age=7 * 86400)
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith(("/static/vendor/", "/static/fonts/")):
            response.headers["Cache-Control"] = "public, max-age=86400"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        return response

    app.include_router(router)
    app.include_router(build_router(get_conn))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
