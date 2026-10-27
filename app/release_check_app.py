"""Standalone Release API and the compiled browser interface."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.routers.release_check import router

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'frontend' / 'dist'
app = FastAPI(title='Datalog Check')
app.include_router(router)


@app.get('/health', include_in_schema=False)
def health():
    return {'application': 'datalog-check', 'root': str(ROOT.resolve())}


@app.get('/', include_in_schema=False)
def home():
    return RedirectResponse('/release-check')


@app.get('/release-check', include_in_schema=False)
def page():
    return FileResponse(DIST / 'index.html')


if DIST.is_dir():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')
