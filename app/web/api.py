"""API JSON e WebSocket da extensão (servidos no mesmo processo da interface)."""
from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..adapters.browser import ponte_navegador
from ..config import settings
from ..db import buscar_um, de_json, listar
from ..engine import chatbot

api_router = APIRouter()


@api_router.post("/api/chat")
def enviar_chat(corpo: dict):
    texto = (corpo.get("texto") or "").strip()
    if not texto:
        return {"erro": "mensagem vazia"}
    chatbot.registrar_usuario(texto)
    respostas = chatbot.responder(texto)
    return {
        "respostas": [
            {
                "texto": m.texto,
                "acoes": m.acoes,
                "tarefa_id": m.tarefa_id,
                "tipo": m.tipo,
                "id": chatbot.registrar_agente(m),
            }
            for m in respostas
        ]
    }


@api_router.get("/api/chat/mensagens")
def mensagens_chat(depois: int = 0):
    chatbot.puxar_notificacoes()
    linhas = listar("chat_msg", where="id > ?", parametros=(depois,), ordem="id ASC", limite=100)
    return {
        "mensagens": [
            {
                "id": l["id"],
                "papel": l["papel"],
                "texto": l["texto"],
                "acoes": de_json(l["acoes_json"], []),
                "ts": l["ts"],
            }
            for l in linhas
        ]
    }


@api_router.get("/api/tarefa/{tarefa_id}/estado")
def estado_tarefa(tarefa_id: int):
    tarefa = buscar_um("tarefa", tarefa_id)
    if tarefa is None:
        return {"erro": "tarefa não encontrada"}, 404
    eventos = listar(
        "tarefa_evento",
        where="tarefa_id = ?",
        parametros=(tarefa_id,),
        ordem="id DESC",
        limite=12,
    )
    return {
        "id": tarefa_id,
        "estado": tarefa["estado"],
        "progresso": tarefa["progresso"],
        "erro": tarefa["erro"],
        "eventos": [
            {
                "ts": e["ts"],
                "nivel": e["nivel"],
                "etapa": e["etapa"],
                "mensagem": e["mensagem"],
            }
            for e in reversed(eventos)
        ],
    }


@api_router.get("/api/extensao/status")
def status_extensao():
    pendentes = listar("pedido_extensao", where="estado = 'pendente'", ordem="id ASC")
    return ponte_navegador.status() | {"pedidos_pendentes": len(pendentes)}


@api_router.get("/api/pedido/{pedido_id}")
def estado_pedido(pedido_id: int):
    pedido = buscar_um("pedido_extensao", pedido_id)
    if pedido is None:
        return {"erro": "pedido não encontrado"}
    return {
        "id": pedido["id"],
        "tipo": pedido["tipo"],
        "estado": pedido["estado"],
        "resposta": de_json(pedido["resposta_json"], {}),
    }


@api_router.websocket(settings.caminho_ws_extensao)
async def ws_extensao(websocket: WebSocket, token: str = Query(default="")):
    if settings.token_extensao and token != settings.token_extensao:
        await websocket.close(code=4001, reason="token inválido")
        return
    await websocket.accept()
    try:
        await ponte_navegador.atender(websocket)
    except WebSocketDisconnect:
        pass
