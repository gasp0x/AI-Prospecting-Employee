"""Conector Nominatim/OpenStreetMap — descoberta de estabelecimentos reais.

O Nominatim é uma fonte pública com política de uso documentada
(https://operations.osmfoundation.org/policies/nominatim/): máximo 1 req/s,
User-Agent identificando a aplicação. O conector respeita os dois limites.

Cada resultado vira um candidato com: nome, endereço formatado, categoria,
site (quando registrado no OSM) e a URL do objeto OSM como fonte verificável.
"""
from __future__ import annotations

import time

import httpx

from ..config import settings

_URL_BUSCA = "https://nominatim.openstreetmap.org/search"
_INTERVALO_MINIMO_S = 1.1  # política: 1 req/s
_ultima_requisicao = 0.0


class FonteBloqueada(Exception):
    """A fonte pública recusou o acesso (bloqueio/rate-limit) — parar com transparência."""


def _respeitar_intervalo() -> None:
    global _ultima_requisicao
    decorrido = time.monotonic() - _ultima_requisicao
    if decorrido < _INTERVALO_MINIMO_S:
        time.sleep(_INTERVALO_MINIMO_S - decorrido)
    _ultima_requisicao = time.monotonic()


_CLASSES_DE_ESTABELECIMENTO = {
    "amenity", "shop", "office", "healthcare", "leisure", "craft", "tourism", "club",
}


def buscar(consulta: str, limite: int = 12, somente_brasil: bool = True) -> list[dict]:
    """Busca POIs por termo (ex.: "fisioterapia Campinas"). Retorna candidatos.

    Aceita apenas objetos do OSM que são estabelecimentos (classe amenity/shop/
    office/healthcare/...), descartando ruas e praças que apenas CONTENHAM o
    termo no nome (ex.: "Rua Advogado X" numa busca por advogados).
    """
    global _ultima_requisicao
    parametros = {
        "q": consulta,
        "format": "jsonv2",
        "limit": str(limite),
        "addressdetails": "1",
        "extratags": "1",
    }
    if somente_brasil:
        parametros["countrycodes"] = "br"
    _respeitar_intervalo()
    resposta = httpx.get(
        _URL_BUSCA,
        params=parametros,
        headers={"User-Agent": settings.agente_usuario, "Accept-Language": "pt-BR"},
        timeout=settings.timeout_busca_s,
        follow_redirects=True,
    )
    if resposta.status_code in (403, 429):
        raise FonteBloqueada(f"HTTP {resposta.status_code} (limite de uso/bloqueio da fonte pública)")
    resposta.raise_for_status()
    try:
        dados = resposta.json()
    except ValueError as erro:
        raise FonteBloqueada("resposta não-JSON (possível página de bloqueio)") from erro

    candidatos: list[dict] = []
    for item in dados:
        tipo = item.get("osm_type")
        id_osm = item.get("osm_id")
        # jsonv2 expõe "category"; "class" existia no formato json antigo
        classe = item.get("category") or item.get("class") or ""
        if not tipo or not id_osm:
            continue
        if classe not in _CLASSES_DE_ESTABELECIMENTO:
            continue  # ruas, praças, bairros etc. não são empresas
        endereco = item.get("display_name", "")
        partes = [p.strip() for p in endereco.split(",")]
        cidade = _campo_endereco(item, ("city", "town", "village", "municipality")) or (partes[-4] if len(partes) >= 4 else "")
        uf = _campo_endereco(item, ("state",))
        extra = item.get("extratags") or {}
        site = extra.get("website") or extra.get("contact:website") or None
        nome = item.get("name") or partes[0]
        if not nome:
            continue
        exibicao = f"{cidade}{'/' + uf if uf else ''}" if cidade or uf else endereco[:120]
        candidatos.append(
            {
                "chave": f"{tipo}/{id_osm}",
                "nome": nome,
                "endereco": endereco[:300],
                "cidade": cidade,
                "uf": uf,
                "localizacao": exibicao,
                "categoria": item.get("type") or classe or None,
                "site": site,
                "fonte_url": f"https://www.openstreetmap.org/{tipo}/{id_osm}",
                "lat": item.get("lat"),
                "lon": item.get("lon"),
            }
        )
    return candidatos


def _campo_endereco(item: dict, chaves: tuple[str, ...]) -> str:
    endereco = item.get("address") or {}
    for chave in chaves:
        if endereco.get(chave):
            return endereco[chave]
    return ""
