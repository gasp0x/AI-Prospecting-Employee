"""Aplicação desktop: janela nativa (WKWebView) + servidor embutido.

Um único processo: sobe o servidor FastAPI em uma thread e abre a janela
nativa do macOS apontando para ele. Ao fechar a janela, o servidor encerra.

Execução: `.venv/bin/python -m app.desktop`  (ou pelo .app em dist/)
Se o servidor já estiver rodando na porta, a janela apenas o reaproveita.
"""
from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn
import webview

from .config import settings


def porta_em_uso(porta: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as soquete:
        soquete.settimeout(0.3)
        return soquete.connect_ex((host, porta)) == 0


def iniciar_servidor() -> uvicorn.Server:
    from .main import app

    config = uvicorn.Config(app, host=settings.host, port=settings.porta, log_level="warning")
    servidor = uvicorn.Server(config)
    threading.Thread(target=servidor.run, name="servidor-local", daemon=True).start()
    for _ in range(60):  # espera o servidor aceitar conexões (até ~12s)
        if porta_em_uso(settings.porta):
            return servidor
        time.sleep(0.2)
    return servidor


def main() -> int:
    ja_rodando = porta_em_uso(settings.porta)
    servidor = None
    if not ja_rodando:
        servidor = iniciar_servidor()

    url = f"http://{settings.host}:{settings.porta}/"
    webview.create_window(
        "MAA",
        url,
        width=1280,
        height=880,
        min_size=(900, 600),
    )
    webview.start(debug=bool(__import__("os").environ.get("AIPE_DEBUG")))

    if servidor is not None:
        servidor.should_exit = True
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
