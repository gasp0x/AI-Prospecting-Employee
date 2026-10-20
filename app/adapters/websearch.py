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
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import settings

_URL_DDG = "https://html.duckduckgo.com/html/"
_URL_BRAVE = "https://search.brave.com/search"

# resfriamento: motor bloqueado fica pausado por N segundos (evita martelar o limite)
_COOLDOWN_S = 90
_bloqueio_ate: dict[str, float] = {"duckduckgo": 0.0, "brave": 0.0}
_lock = threading.Lock()


def _em_cooldown(nome: str) -> bool:
    with _lock:
        return time.monotonic() < _bloqueio_ate.get(nome, 0.0)


def _aplicar_cooldown(nome: str) -> None:
    with _lock:
        _bloqueio_ate[nome] = time.monotonic() + _COOLDOWN_S
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


def reset_cooldown() -> None:
    """Limpa o resfriamento (usado em testes e útil para diagnóstico)."""
    with _lock:
        _bloqueio_ate.update({"duckduckgo": 0.0, "brave": 0.0})


def buscar(consulta: str, limite: int = 8) -> list[str]:
    """Retorna URLs orgânicas da consulta tentando a cadeia de motores.

    Levanta `BuscaBloqueada` apenas se todos os motores falharem.
    """
    falhas: list[str] = []
    for nome, funcao in (("duckduckgo", _buscar_ddg), ("brave", _buscar_brave)):
        if _em_cooldown(nome):
            falhas.append(f"{nome}: em resfriamento após bloqueio recente (tenta de novo em instantes)")
            continue
        try:
            urls = funcao(consulta, limite)
        except BuscaBloqueada as erro:
            _aplicar_cooldown(nome)
            falhas.append(f"{nome}: {erro}")
            continue
        except httpx.HTTPError as erro:
            falhas.append(f"{nome}: {type(erro).__name__}: {erro}")
            continue
        if urls:
            return urls
        falhas.append(f"{nome}: sem resultados")
    raise BuscaBloqueada("; ".join(falhas))


def buscar_detalhado(consulta: str, limite: int = 6) -> list[dict]:
    """Resultados da web com título e URL (pessoas, profissionais, temas).

    Cadeia Brave → DuckDuckGo; levanta `BuscaBloqueada` se todos falharem.
    """
    falhas: list[str] = []
    for nome, funcao in (("brave", _brave_detalhado), ("duckduckgo", _ddg_detalhado)):
        if _em_cooldown(nome):
            falhas.append(f"{nome}: em resfriamento após bloqueio recente (tenta de novo em instantes)")
            continue
        try:
            resultados = funcao(consulta, limite)
        except BuscaBloqueada as erro:
            _aplicar_cooldown(nome)
            falhas.append(f"{nome}: {erro}")
            continue
        except httpx.HTTPError as erro:
            falhas.append(f"{nome}: {type(erro).__name__}: {erro}")
            continue
        if resultados:
            return resultados
        falhas.append(f"{nome}: sem resultados")
    raise BuscaBloqueada("; ".join(falhas))


def _brave_detalhado(consulta: str, limite: int) -> list[dict]:
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

    resultados: list[dict] = []
    for bloco in re.finditer(
        r'<div class="snippet svelte[^"]*" data-pos="\d+" data-type="web".*?(?=<div class="snippet svelte|</section>)',
        resposta.text,
        re.DOTALL,
    ):
        corpo = bloco.group(0)
        ancora = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', corpo, re.DOTALL)
        if not ancora:
            continue
        url = html_mod.unescape(ancora.group(1)).strip()
        dominio = urlparse(url).netloc.lower()
        if not dominio or "brave" in dominio:
            continue
        titulo = re.sub(r"<[^>]+>", " ", ancora.group(2))
        titulo = re.sub(r"\s+", " ", html_mod.unescape(titulo)).strip()
        if url not in {r["url"] for r in resultados}:
            resultados.append({"url": url, "titulo": titulo[:140]})
        if len(resultados) >= limite:
            break
    return resultados


def _ddg_detalhado(consulta: str, limite: int) -> list[dict]:
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

    resultados: list[dict] = []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', resposta.text, re.DOTALL
    ):
        href = _limpar(m.group(1))
        if "y.js" in href or "ad_domain=" in href:
            continue
        if "uddg=" in href:
            try:
                query = parse_qs(urlparse(href).query)
                href = unquote(query.get("uddg", [""])[0])
            except ValueError:
                continue
        titulo = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if href.startswith("http") and href not in {r["url"] for r in resultados}:
            resultados.append({"url": href, "titulo": titulo[:140]})
        if len(resultados) >= limite:
            break
    return resultados


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
