"""Cliente LLM opcional (endpoint compatível com OpenAI).

O MVP funciona 100% sem LLM (modo determinístico). Se `LLM_BASE_URL`,
`LLM_API_KEY` e `LLM_MODELO` estiverem definidos em `.env`, os módulos de
interpretação de ICP e redação podem usar o modelo como auxílio — sempre com
fallback determinístico. O LLM NUNCA gera empresas ou evidências: todo fato
vem de fontes reais coletadas pelos conectores.
"""
from __future__ import annotations

import json
import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def disponivel() -> bool:
    return bool(settings.llm_base_url and settings.llm_api_key and settings.llm_modelo)


def completar_json(system: str, user: str, timeout_s: float = 30.0) -> dict | list | None:
    """Pede uma resposta JSON ao LLM; retorna None em qualquer falha (fallback)."""
    if not disponivel():
        return None
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    corpo = {
        "model": settings.llm_modelo,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        resposta = httpx.post(
            url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=corpo,
            timeout=timeout_s,
        )
        resposta.raise_for_status()
        conteudo = resposta.json()["choices"][0]["message"]["content"]
        return json.loads(conteudo)
    except Exception as erro:  # noqa: BLE001 — LLM é opcional; qualquer falha cai no fallback
        log.warning("LLM indisponível (%s); seguindo em modo determinístico.", erro)
        return None
