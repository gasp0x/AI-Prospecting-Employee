"""Ponte com o OpenClaw: cérebro generalista opcional "por baixo dos panos".

Se o binário do OpenClaw existir, mensagens livres do chat podem ser
encaminhadas ao agente dele (`openclaw agent --local`) e a resposta volta
para o nosso chat. Requisitos: OpenClaw instalado + provedor de LLM
configurado no próprio OpenClaw (`openclaw onboard`).

O OpenClaw NUNCA é usado para a prospecção (evidências, pontuação e
aprovações continuam sendo nossas) — apenas para conversas livres.
"""
from __future__ import annotations

import json
import os
import subprocess

from ..config import settings

_BINARIO_PADRAO = os.path.expanduser("~/.openclaw-npm/node_modules/.bin/openclaw")


def binario() -> str:
    return settings.openclaw_bin or _BINARIO_PADRAO


def disponivel() -> bool:
    return os.path.isfile(binario())


def consultar(mensagem: str, timeout_s: int = 120) -> tuple[str | None, str | None]:
    """Envia a mensagem ao agente do OpenClaw. Retorna (resposta, erro).

    - (texto, None) em caso de sucesso;
    - (None, erro) quando o OpenClaw não está configurado (ex.: falta de
      chave do provedor) ou falhou.
    """
    if not disponivel():
        return None, "OpenClaw não está instalado"
    try:
        processo = subprocess.run(
            [binario(), "agent", "--local", "-m", mensagem, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
    except (subprocess.SubprocessError, OSError) as erro:
        return None, f"falha ao executar o OpenClaw: {erro}"

    try:
        dados = json.loads(processo.stdout)
    except ValueError:
        return None, f"resposta inválida do OpenClaw: {processo.stdout[:200]}"

    if not dados.get("ok"):
        erro = (dados.get("error") or {}).get("message", "erro desconhecido")
        if "authentication" in erro.lower() or "credentials" in erro.lower():
            return None, (
                "OpenClaw sem chave de LLM configurada — rode `"
                + binario() + " onboard` uma vez e cole a chave do seu provedor"
            )
        return None, erro

    for campo in ("reply", "result", "text", "response"):
        valor = dados.get(campo)
        if isinstance(valor, str) and valor.strip():
            return valor.strip(), None
    # formato desconhecido: devolve o JSON bruto como último recurso
    return json.dumps(dados, ensure_ascii=False)[:2000], None
