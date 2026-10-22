"""Точка входа: `uvicorn app.main:app`."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import Store, router
from .seed import seed_db

STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("GOULASH_DB", "data/goulash.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(path)
        db.init_db(conn)
        if db.is_empty(conn):
            seed_db(conn)
        app.state.store = Store(conn)
        yield
        conn.close()

    app = FastAPI(title="Goulash · Поставщики", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
