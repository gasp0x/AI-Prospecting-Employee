"""Coleta de páginas de empresas: robots.txt, tentativas com backoff e bloqueios.

Política anti-bloqueio:
- `robots.txt` é consultado e respeitado (caminho `/` e o caminho alvo);
- CAPTCHA/challenge (Cloudflare etc.), 403/429/503 e páginas-sucata são
  detectados e reportados como `BloqueioDetectado` — o agente INTERROMPE a
  coleta daquele site e registra a limitação, em vez de tentar evadir;
- tentativas limitadas com backoff ficam a cargo do chamador (`research`).
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings

_MARCADORES_CAPTCHA = [
    "just a moment",           # Cloudflare
    "attention required",      # Cloudflare
    "cf-challenge",
    "cf_chl_opt",
    "captcha",
    "verifique que você é humano",
    "prove you are human",
    "access denied",
    "request unsuccessful. incapsula",
    "ddos protection by",
]


class BloqueioDetectado(Exception):
    """Acesso bloqueado por CAPTCHA/challenge/403 — não tentar evadir."""


@dataclass
class ResultadoColeta:
    url: str
    url_final: str
    estado: str  # ok | robots | bloqueado | erro | nao_html
    http_status: int | None = None
    html: str = ""
    detalhe: str = ""


def _cliente() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=settings.timeout_fetch_s,
        headers={
            "User-Agent": settings.agente_usuario,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
        },
    )


def verificar_robots(url: str) -> tuple[bool, str]:
    """Devolve (permitido, detalhe). Sem robots.txt → permitido."""
    try:
        origem = urlparse(url)
        base = f"{origem.scheme}://{origem.netloc}"
        with _cliente() as cliente:
            resposta = cliente.get(f"{base}/robots.txt")
        if resposta.status_code >= 400:
            return True, "sem robots.txt acessível"
        parser = RobotFileParser()
        parser.parse(resposta.text.splitlines())
        agente = "*"
        permitido = parser.can_fetch(agente, url)
        return permitido, "robots.txt respeitado"
    except httpx.HTTPError as erro:
        return True, f"robots.txt indisponível ({erro.__class__.__name__}) — seguindo"


def coletar_site(url: str) -> ResultadoColeta:
    """Coleta a página inicial do site com verificação de robots e bloqueios."""
    permitido, detalhe_robots = verificar_robots(url)
    if not permitido:
        return ResultadoColeta(url=url, url_final=url, estado="robots", detalhe=detalhe_robots)

    try:
        with _cliente() as cliente:
            resposta = cliente.get(url)
    except httpx.HTTPStatusError:  # não ocorre com follow_redirects sem raise_for_status
        raise
    except httpx.HTTPError as erro:
        return ResultadoColeta(url=url, url_final=url, estado="erro", detalhe=f"{type(erro).__name__}: {erro}")

    if resposta.status_code in (401, 403, 429, 503):
        raise BloqueioDetectado(f"HTTP {resposta.status_code} — a origem recusou o acesso automatizado")

    tipo = resposta.headers.get("content-type", "")
    if "html" not in tipo and "xml" not in tipo:
        return ResultadoColeta(url=url, url_final=str(resposta.url), estado="nao_html", http_status=resposta.status_code, detalhe=f"content-type: {tipo}")

    html = resposta.text[: 2_000_000]
    corpo_baixo = html[:60_000].lower()
    for marcador in _MARCADORES_CAPTCHA:
        if marcador in corpo_baixo:
            raise BloqueioDetectado(f"página de bloqueio/challenge detectada (marcador: “{marcador}”)")
    if resposta.status_code >= 400:
        return ResultadoColeta(url=url, url_final=str(resposta.url), estado="erro", http_status=resposta.status_code, detalhe=f"HTTP {resposta.status_code}")

    return ResultadoColeta(url=url, url_final=str(resposta.url), estado="ok", http_status=resposta.status_code, html=html)
