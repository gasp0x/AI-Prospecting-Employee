"""Aplicação FastAPI do AI Prospecting Employee (monólito modular local).

Um único processo serve: interface web pt-BR, API JSON e o WebSocket da
extensão do navegador. Execução: `python3 -m app.main` (ou uvicorn app.main:app).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .web.api import api_router
from .web.routes import router as paginas_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    logging.info("Banco de dados pronto em %s", settings.caminho_banco)
    if settings.token_telegram:
        from .adapters.telegram import iniciar_se_configurado

        iniciar_se_configurado()
        logging.info("Canal Telegram ativo.")
    yield


app = FastAPI(title="AI Prospecting Employee", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

app.include_router(paginas_router)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    db.init_schema()
    uvicorn.run(app, host=settings.host, port=settings.porta, log_level="info")
