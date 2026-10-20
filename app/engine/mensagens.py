"""Mensagem do chat e persistência — módulo neutro (sem dependências do motor)."""
from __future__ import annotations

from .. import db


class Mensagem:
    def __init__(self, texto: str, acoes: list[dict] | None = None, tarefa_id: int | None = None, tipo: str = "texto"):
        self.texto = texto
        self.acoes = acoes or []
        self.tarefa_id = tarefa_id
        self.tipo = tipo


def registrar_usuario(texto: str, canal: str = "web") -> int:
    return db.inserir("chat_msg", {"papel": "usuario", "texto": texto, "ts": db.agora()})


def registrar_agente(msg: Mensagem, tarefa_id: int | None = None) -> int:
    return db.inserir(
        "chat_msg",
        {
            "papel": "agente",
            "texto": msg.texto,
            "acoes_json": db.para_json(msg.acoes) if msg.acoes else None,
            "tarefa_id": msg.tarefa_id or tarefa_id,
            "ts": db.agora(),
        },
    )
