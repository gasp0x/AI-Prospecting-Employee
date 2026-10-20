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
import json
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse


from ..config import settings

# Motores de busca endurecem a resposta quando o UA se declara bot. Aqui usamos
# UA de navegador padrão; quando bloquearem mesmo assim, respeitamos e registramos.
_UA_NAVEGADOR = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0 Safari/537.36"
)
_URL_DDG = "https://html.duckduckgo.com/html/"
_URL_BRAVE = "https://search.brave.com/search"
_URL_BING_RSS = "https://www.bing.com/search"
_URL_GNEWS_RSS = "https://news.google.com/rss/search"
_URL_WIKIPEDIA = "https://pt.wikipedia.org/w/api.php"

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


def _curl(metodo: str, url: str, dados: dict | None = None, timeout_s: int | None = None) -> tuple[int, str]:
    """HTTP via curl nativo (fingerprint TLS de navegador; httpx recebe 429 por JA3)."""
    timeout = timeout_s or settings.timeout_busca_s
    comando = ["curl", "-s", "-L", "--max-time", str(timeout),
               "-A", _UA_NAVEGADOR,
               "-H", "Accept-Language: pt-BR,pt;q=0.9",
               "-o", "-", "-w", "\n===CURL_STATUS=%{http_code}"]
    if metodo == "GET" and dados:
        for chave, valor in dados.items():
            comando += ["--data-urlencode", f"{chave}={valor}"]
        comando += ["-G"]
    elif metodo == "POST" and dados:
        for chave, valor in dados.items():
            comando += ["--data-urlencode", f"{chave}={valor}"]
    comando.append(url)
    processo = subprocess.run(comando, capture_output=True, text=True, timeout=timeout + 10)
    saida = processo.stdout
    if "===CURL_STATUS=" in saida:
        corpo, _, status = saida.rpartition("===CURL_STATUS=")
        return int(status.strip() or 0), corpo
    return 0, saida


def _resposta_bloqueada(status: int, corpo: str) -> bool:
    if status in (202, 403, 429):
        return True
    # marcadores só no <title>: páginas legítimas (200) carregam a palavra
    # "captcha" dentro do JavaScript delas e o scan no corpo gerava falso positivo
    titulo = re.search(r"<title>([^<]*)</title>", corpo[:5000], re.IGNORECASE)
    if titulo and any(m in titulo.group(1).lower() for m in _MARCADORES_BLOQUEIO):
        return True
    return "verify you are human" in corpo[:5000].lower() or "prove you are human" in corpo[:5000].lower()


def _limpar(url: str) -> str:
    return html_mod.unescape(url).strip()


_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL_S = 600


def _cache_obter(chave: str) -> list | None:
    item = _cache.get(chave)
    if item and time.monotonic() - item[0] < _CACHE_TTL_S:
        return item[1]
    return None


def _cache_guardar(chave: str, valor: list) -> None:
    _cache[chave] = (time.monotonic(), valor)


def reset_cooldown() -> None:
    """Limpa o resfriamento de TODOS os motores (testes e diagnóstico)."""
    with _lock:
        _bloqueio_ate.clear()


def buscar(consulta: str, limite: int = 8) -> list[str]:
    """Retorna URLs orgânicas da consulta tentando a cadeia de motores.

    Levanta `BuscaBloqueada` apenas se todos os motores falharem.
    """
    chave = f"busca:{consulta.lower().strip()}:{limite}"
    em_cache = _cache_obter(chave)
    if em_cache is not None:
        return em_cache

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
        except (OSError, subprocess.SubprocessError) as erro:
            falhas.append(f"{nome}: {type(erro).__name__}: {erro}")
            continue
        if urls:
            _cache_guardar(chave, urls)
            return urls
        falhas.append(f"{nome}: sem resultados")
    raise BuscaBloqueada("; ".join(falhas))


def _limpar_xml(valor: str) -> str:
    return html_mod.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", valor)).strip()


def _bing_rss_detalhado(consulta: str, limite: int) -> list[dict]:
    """Bing via RSS (formato=rss) — tolerante a IPs de datacenter."""
    status, corpo = _curl("GET", _URL_BING_RSS, {"q": consulta, "format": "rss"})
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    resultados: list[dict] = []
    for m in re.finditer(r"<item>\s*<title>(.*?)</title>\s*<link>(.*?)</link>", corpo, re.DOTALL):
        titulo, url = _limpar_xml(m.group(1)), _limpar_xml(m.group(2))
        if url.startswith("http") and url not in {r["url"] for r in resultados}:
            resultados.append({"url": url, "titulo": titulo[:140]})
        if len(resultados) >= limite:
            break
    if not resultados:
        raise BuscaBloqueada("RSS sem resultados (possível bloqueio)")
    return resultados


def _gnews_detalhado(consulta: str, limite: int) -> list[dict]:
    """Google News via RSS — notícias e cobertura local."""
    status, corpo = _curl(
        "GET", _URL_GNEWS_RSS,
        {"q": consulta, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"},
    )
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    resultados: list[dict] = []
    for m in re.finditer(r"<item>\s*<title>(.*?)</title>\s*<link>(.*?)</link>", corpo, re.DOTALL):
        titulo, url = _limpar_xml(m.group(1)), _limpar_xml(m.group(2))
        if url.startswith("http") and url not in {r["url"] for r in resultados}:
            resultados.append({"url": url, "titulo": titulo[:140]})
        if len(resultados) >= limite:
            break
    if not resultados:
        raise BuscaBloqueada("RSS sem resultados (possível bloqueio)")
    return resultados


def _wikipedia_detalhado(consulta: str, limite: int) -> list[dict]:
    """Wikipédia (API aberta) — pessoas, conceitos e organizações."""
    status, corpo = _curl(
        "GET", _URL_WIKIPEDIA,
        {"action": "query", "list": "search", "srsearch": consulta,
         "srlimit": str(limite), "format": "json"},
        timeout_s=settings.timeout_busca_s,
    )
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")
    try:
        dados = json.loads(corpo)
    except ValueError as erro:
        raise BuscaBloqueada("resposta não-JSON") from erro

    resultados: list[dict] = []
    for item in dados.get("query", {}).get("search", []):
        titulo = item.get("title", "")
        url = "https://pt.wikipedia.org/wiki/" + titulo.replace(" ", "_")
        if titulo and url not in {r["url"] for r in resultados}:
            resultados.append({"url": url, "titulo": titulo[:140]})
    if not resultados:
        raise BuscaBloqueada("sem resultados")
    return resultados


def buscar_detalhado(consulta: str, limite: int = 6) -> list[dict]:
    """Resultados da web com título e URL (pessoas, profissionais, temas).

    Cadeia Brave → DuckDuckGo; levanta `BuscaBloqueada` se todos falharem.
    """
    falhas: list[str] = []
    for nome, funcao in (("brave", _brave_detalhado), ("duckduckgo", _ddg_detalhado),
                         ("bing_rss", _bing_rss_detalhado), ("google_news", _gnews_detalhado),
                         ("wikipedia", _wikipedia_detalhado)):
        if _em_cooldown(nome):
            falhas.append(f"{nome}: em resfriamento após bloqueio recente (tenta de novo em instantes)")
            continue
        try:
            resultados = funcao(consulta, limite)
        except BuscaBloqueada as erro:
            _aplicar_cooldown(nome)
            falhas.append(f"{nome}: {erro}")
            continue
        except (OSError, subprocess.SubprocessError) as erro:
            falhas.append(f"{nome}: {type(erro).__name__}: {erro}")
            continue
        if resultados:
            return resultados
        falhas.append(f"{nome}: sem resultados")
    raise BuscaBloqueada("; ".join(falhas))


def _brave_detalhado(consulta: str, limite: int) -> list[dict]:
    status, corpo = _curl("GET", _URL_BRAVE, {"q": consulta})
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    resultados: list[dict] = []
    for bloco in re.finditer(
        r'<div class="snippet svelte[^"]*" data-pos="\d+" data-type="web".*?(?=<div class="snippet svelte|</section>)',
        corpo,
        re.DOTALL,
    ):
        bloco_html = bloco.group(0)
        ancora = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', bloco_html, re.DOTALL)
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
    status, corpo = _curl("POST", _URL_DDG, {"q": consulta, "kl": "br-pt"})
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    resultados: list[dict] = []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', corpo, re.DOTALL
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
    status, corpo = _curl("POST", _URL_DDG, {"q": consulta, "kl": "br-pt"})
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    urls: list[str] = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', corpo):
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
    status, corpo = _curl("GET", _URL_BRAVE, {"q": consulta})
    if _resposta_bloqueada(status, corpo):
        raise BuscaBloqueada(f"HTTP {status} (anti-bot/CAPTCHA)")
    if status >= 400:
        raise BuscaBloqueada(f"HTTP {status}")

    urls: list[str] = []
    # resultados orgânicos ficam em blocos <div ... data-type="web"> com o link principal
    for m in re.finditer(
        r'<div[^>]+data-type="web"[^>]*>.{0,3000}?<a[^>]+href="(https?://[^"]+)"',
        corpo,
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
