from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import area, downloads, extractors, mecheck, pages

app = FastAPI()
static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(pages.router)
app.include_router(downloads.router)
app.include_router(area.router)
app.include_router(mecheck.router)
app.include_router(extractors.router)
