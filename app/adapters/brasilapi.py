"""Enriquecimento por CNPJ via BrasilAPI (dados abertos da Receita Federal).

Endpoint público: GET https://brasilapi.com.br/api/cnpj/v1/{cnpj}
Usado somente quando um CNPJ é REALMENTE encontrado no site da empresa —
nunca gera dados. Falhas são toleradas (registro em evento, sem interromper).
"""
from __future__ import annotations

import httpx

from ..config import settings

_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"


def consultar_cnpj(cnpj_formatado: str) -> dict | None:
    """Consulta dados cadastrais. Retorna dict reduzido ou None (não encontrado)."""
    cnpj = "".join(d for d in cnpj_formatado if d.isdigit())
    if len(cnpj) != 14:
        return None
    resposta = httpx.get(
        _URL.format(cnpj=cnpj),
        headers={"User-Agent": settings.agente_usuario},
        timeout=settings.timeout_busca_s,
    )
    if resposta.status_code != 200:
        return None
    dados = resposta.json()
    return {
        "cnpj": cnpj,
        "razao_social": dados.get("razao_social") or "",
        "municipio": dados.get("municipio") or "",
        "uf": dados.get("uf") or "",
        "cnae_descricao": dados.get("cnae_fiscal_descricao") or "",
        "situacao": dados.get("descricao_situacao_cadastral") or "",
    }
