"""Busca web pública com cadeia de motores e detecção honesta de bloqueio.

Motores, em ordem:
1. DuckDuckGo HTML — bloqueado em IPs de datacenter/VPN (HTTP 202 "anomaly");
2. Brave Search — tolerante a datacenter (validado com NordVPN ativa).

Cada motor tem seu próprio parser e detector de bloqueio; `buscar()` tenta a
cadeia inteira e só levanta `BuscaBloqueada` se TODOS falharem — o agente
registra a limitação e segue com as demais fontes (Nominatim, sites diretos,
navegador do usuário via extensão). Nenhuma tentativa de evasão é feita.
"""
from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import settings

_URL_DDG = "https://html.duckduckgo.com/html/"
_URL_BRAVE = "https://search.brave.com/search"
_MARCADORES_BLOQUEIO = ("anomaly", "captcha", "challenge", "verify you are human", "prove you are human")


class BuscaBloqueada(Exception):
    """Todos os motores de busca recusaram o acesso (anti-bot/CAPTCHA)."""


def _resposta_bloqueada(status: int, corpo: str) -> bool:
    if status in (202, 403, 429):
        return True
    corpo_baixo = corpo[:200_000].lower()
    return any(m in corpo_baixo for m in _MARCADORES_BLOQUEIO)


def _limpar(url: str) -> str:
    return html_mod.unescape(url).strip()


def buscar(consulta: str, limite: int = 8) -> list[str]:
    """Retorna URLs orgânicas da consulta tentando a cadeia de motores.

    Levanta `BuscaBloqueada` apenas se todos os motores falharem.
    """
    falhas: list[str] = []
    for nome, funcao in (("duckduckgo", _buscar_ddg), ("brave", _buscar_brave)):
        try:
            urls = funcao(consulta, limite)
        except BuscaBloqueada as erro:
            falhas.append(f"{nome}: {erro}")
            continue
        except httpx.HTTPError as erro:
            falhas.append(f"{nome}: {type(erro).__name__}: {erro}")
            continue
        if urls:
            return urls
        falhas.append(f"{nome}: sem resultados")
    raise BuscaBloqueada("; ".join(falhas))


def _buscar_ddg(consulta: str, limite: int) -> list[str]:
    resposta = httpx.post(
        _URL_DDG,
        data={"q": consulta, "kl": "br-pt"},
        headers={"User-Agent": settings.agente_usuario},
        timeout=settings.timeout_busca_s,
        follow_redirects=True,
    )
    if _resposta_bloqueada(resposta.status_code, resposta.text):
        raise BuscaBloqueada(f"HTTP {resposta.status_code} (anti-bot/CAPTCHA)")
    resposta.raise_for_status()

    urls: list[str] = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', resposta.text):
        href = _limpar(m.group(1))
        # anúncios (y.js?ad_domain=...) e links encapsulados uddg
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


def _buscar_brave(consulta: str, limite: int) -> list[str]:
    resposta = httpx.get(
        _URL_BRAVE,
        params={"q": consulta},
        headers={"User-Agent": settings.agente_usuario, "Accept-Language": "pt-BR,pt;q=0.9"},
        timeout=settings.timeout_busca_s,
        follow_redirects=True,
    )
    if _resposta_bloqueada(resposta.status_code, resposta.text):
        raise BuscaBloqueada(f"HTTP {resposta.status_code} (anti-bot/CAPTCHA)")
    resposta.raise_for_status()

    urls: list[str] = []
    # resultados orgânicos ficam em blocos <div ... data-type="web"> com o link principal
    for m in re.finditer(
        r'<div[^>]+data-type="web"[^>]*>.{0,3000}?<a[^>]+href="(https?://[^"]+)"',
        resposta.text,
        re.DOTALL,
    ):
        href = _limpar(m.group(1))
        dominio = urlparse(href).netloc.lower()
        if not dominio or "brave" in dominio:
            continue
        if href not in urls:
            urls.append(href)
        if len(urls) >= limite:
            break
    return urls
