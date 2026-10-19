"""Busca web pública (DuckDuckGo HTML) com detecção honesta de bloqueio.

Muitos ambientes (IP de datacenter, por exemplo) recebem uma página de
"anomaly detection" em vez de resultados. Este conector:
- detecta a página de bloqueio (HTTP 202 + marcadores "anomaly"/"captcha");
- levanta `BuscaBloqueada` para o agente registrar a limitação e seguir com as
  demais fontes (Nominatim, sites diretos, navegador do usuário via extensão).

Nenhuma tentativa de evasão é feita — é política do projeto parar e explicar.
"""
from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import settings

_URL = "https://html.duckduckgo.com/html/"
_MARCADORES = ("anomaly", "captcha", "challenge", "verify you are human")


class BuscaBloqueada(Exception):
    """Motor de busca recusou o acesso (anti-bot/CAPTCHA)."""


def buscar(consulta: str, limite: int = 8) -> list[str]:
    """Retorna URLs orgânicas da consulta; levanta BuscaBloqueada se bloqueado."""
    resposta = httpx.post(
        _URL,
        data={"q": consulta, "kl": "br-pt"},
        headers={"User-Agent": settings.agente_usuario},
        timeout=settings.timeout_busca_s,
        follow_redirects=True,
    )
    corpo = resposta.text[:200_000].lower()
    if resposta.status_code in (202, 403, 429) or any(m in corpo for m in _MARCADORES):
        raise BuscaBloqueada(f"motor de busca retornou bloqueio (HTTP {resposta.status_code}, anti-bot/CAPTCHA)")
    resposta.raise_for_status()

    urls: list[str] = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', resposta.text):
        href = html_mod.unescape(m.group(1)).strip()
        # links do DDG vêm encapsulados em /l/?uddg=<url codificada>; anúncios
        # vêm como y.js?ad_domain=... e são descartados (não são resultados orgânicos)
        if "y.js" in href or "ad_domain=" in href:
            continue
        if "uddg=" in href:
            try:
                query = parse_qs(urlparse(href).query)
                href = unquote(query.get("uddg", [""])[0])
            except ValueError:
                continue
        if href.startswith("http") and href not in urls:
            urls.append(href)
        if len(urls) >= limite:
            break
    return urls
