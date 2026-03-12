"""GET routes for HTML pages (index, me-check, e-055, e-251, e-142, area)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/me-check", response_class=HTMLResponse)
async def read_me_check(request: Request):
    return templates.TemplateResponse(request, "me-check.html")


@router.get("/me-check/develop", response_class=HTMLResponse)
async def read_develop(request: Request):
    return templates.TemplateResponse(request, "develop.html")


@router.get("/e-055", response_class=HTMLResponse)
async def read_e055(request: Request):
    return templates.TemplateResponse(request, "e-055.html")


@router.get("/e-251", response_class=HTMLResponse)
async def read_e251(request: Request):
    return templates.TemplateResponse(request, "e-251.html")


@router.get("/e-142", response_class=HTMLResponse)
async def read_e142(request: Request):
    return templates.TemplateResponse(request, "e-142.html")


@router.get("/area", response_class=HTMLResponse)
async def read_area(request: Request):
    return templates.TemplateResponse(request, "area.html")
