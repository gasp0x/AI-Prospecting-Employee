"""Integração Slack: envia mensagens via API oficial (bot token).

Requisitos (uma vez):
1. Crie um app em https://api.slack.com/apps → "From scratch";
2. Em "OAuth & Permissions", adicione os escopos de bot:
   chat:write, users:read, im:write, channels:read;
3. "Install to Workspace" e copie o token xoxb-…;
4. Cole no `.env`: SLACK_BOT_TOKEN=xoxb-… e reinicie.

Para o bot escrever em um canal, é preciso convidá-lo: /invite @Wilson no canal.
"""
from __future__ import annotations

import re
import unicodedata

import httpx

from ..config import settings

_URL = "https://slack.com/api"


def configurado() -> bool:
    return bool(settings.slack_bot_token)


def _chamar(metodo: str, payload: dict | None = None) -> dict:
    resposta = httpx.post(
        f"{_URL}/{metodo}",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}",
                 "Content-Type": "application/json; charset=utf-8"},
        json=payload or {},
        timeout=20,
    )
    dados = resposta.json()
    if not dados.get("ok"):
        raise ValueError(f"Slack respondeu erro: {dados.get('error', dados)}")
    return dados


def _get(metodo: str, parametros: dict | None = None) -> dict:
    resposta = httpx.get(
        f"{_URL}/{metodo}",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        params=parametros or {},
        timeout=20,
    )
    dados = resposta.json()
    if not dados.get("ok"):
        raise ValueError(f"Slack respondeu erro: {dados.get('error', dados)}")
    return dados


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn").lower().strip()


def usuarios() -> list[dict]:
    """Usuários humanos do workspace: [{id, nome, usuario}]."""
    saida: list[dict] = []
    cursor = ""
    for _ in range(4):  # até 4 páginas de 200
        dados = _get("users.list", {"limit": 200, "cursor": cursor})
        for u in dados.get("members", []):
            if u.get("is_bot") or u.get("id") in ("USLACKBOT",):
                continue
            nome = u.get("real_name") or u.get("name") or ""
            saida.append({"id": u["id"], "nome": nome, "usuario": u.get("name", "")})
        cursor = dados.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return saida


def resolver_destino(destino: str) -> dict:
    """Resolve o destino: "#canal" → canal; "arthur" → usuário por nome."""
    destino = destino.strip()
    if destino.startswith("#"):
        canais = _get("conversations.list", {"types": "public_channel,private_channel", "limit": 200})
        for canal in canais.get("channels", []):
            if _norm(canal.get("name", "")) == _norm(destino[1:]):
                return {"tipo": "canal", "id": canal["id"], "nome": canal["name"]}
        raise ValueError(f"canal “{destino}” não encontrado (o bot precisa ser convidado a ele)")
    alvo = _norm(destino).lstrip("@")
    for u in usuarios():
        if alvo in _norm(u["usuario"]) or alvo in _norm(u["nome"]):
            dm = _chamar("conversations.open", {"users": u["id"]})
            canal_id = dm["channel"]["id"]
            return {"tipo": "dm", "id": canal_id, "nome": u["nome"] or u["usuario"]}
    raise ValueError(f"usuário “{destino}” não encontrado no workspace")


def enviar_para(destino: str, texto: str) -> dict:
    """Envia a mensagem para "#canal" ou para uma pessoa (DM por nome)."""
    alvo = resolver_destino(destino)
    dados = _chamar("chat.postMessage", {"channel": alvo["id"], "text": texto})
    return {"ok": True, "destino": alvo["nome"], "canal_id": alvo["id"], "ts": dados.get("ts")}


def enviar_webhook(texto: str) -> dict:
    """Envia via Incoming Webhook (URL fixa de um canal)."""
    if not settings.slack_webhook_url:
        raise ValueError("webhook do Slack não configurado")
    resposta = httpx.post(settings.slack_webhook_url, json={"text": texto}, timeout=20)
    if resposta.status_code != 200:
        raise ValueError(f"webhook respondeu HTTP {resposta.status_code}")
    return {"ok": True, "via": "webhook"}
