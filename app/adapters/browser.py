"""Ponte com o navegador do usuário (extensão autorizada via WebSocket local).

Fluxo de autorização:
1. O usuário instala a extensão (Manifest V3) no navegador DELE — o mesmo
   perfil onde as sessões autenticadas já existem.
2. A extensão conecta em ws://127.0.0.1:<porta>/ws/extensao (apenas local).
3. Todo pedido do agente (ler a aba ativa / abrir URL) aparece no popup da
   extensão e só é executado após clique explícito em “Permitir”.

Privacidade: a extensão nunca lê cookies, senhas ou localStorage — somente o
texto visível da aba que o usuário autorizar naquele momento. Nada sai da
máquina: a ponte é um WebSocket em 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import db


class BrowserBridge:
    def __init__(self) -> None:
        self._ws: WebSocket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._info_navegador: dict = {}
        self._futures: dict[int, asyncio.Future] = {}

    # ---------- estado ----------
    def status(self) -> dict:
        with self._lock:
            conectado = self._ws is not None
            info = dict(self._info_navegador)
        return {
            "conectado": conectado,
            "navegador": info.get("navegador", ""),
            "perfil": info.get("perfil", ""),
            "conectado_em": info.get("conectado_em", ""),
        }

    # ---------- ciclo de vida da conexão ----------
    async def atender(self, ws: WebSocket) -> None:
        with self._lock:
            if self._ws is not None:
                await ws.close(code=4000, reason="já existe uma conexão ativa")
                return
            self._ws = ws
            self._loop = asyncio.get_running_loop()
            self._info_navegador = {}
        db.registrar_auditoria("extensao", "extensao_conectada", {})
        try:
            # entrega o backlog de pedidos pendentes logo na conexão
            for pedido in db.listar("pedido_extensao", where="estado = 'pendente'", ordem="id ASC"):
                await self._enviar(db.de_json(pedido["params_json"], {}) | {"id": pedido["id"], "tipo": pedido["tipo"]})
            while True:
                mensagem = await ws.receive_json()
                if "ola" in mensagem:
                    with self._lock:
                        self._info_navegador = mensagem["ola"]
                    await self._bruto({"pong": db.agora()})
                elif "ping" in mensagem:
                    await self._bruto({"pong": db.agora()})
                elif "resposta" in mensagem:
                    self._receber_resposta(mensagem["resposta"])
        except WebSocketDisconnect:
            pass
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws = None
            db.registrar_auditoria("extensao", "extensao_desconectada", {})

    async def _bruto(self, payload: dict) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("extensão não conectada")
        await ws.send_json(payload)

    async def _enviar(self, pedido: dict) -> None:
        try:
            await self._bruto({"pedido": pedido})
        except RuntimeError:
            pass  # desconectou entre o check e o envio; pedido permanece pendente

    # ---------- criação de pedidos (chamado por rotas/engenharia) ----------
    def criar_pedido(self, tipo: str, params: dict) -> int:
        pedido_id = db.inserir(
            "pedido_extensao",
            {"tipo": tipo, "params_json": db.para_json(params), "criado_em": db.agora()},
        )
        with self._lock:
            ws = self._ws
            loop = self._loop
        if ws is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(self._enviar({"id": pedido_id, "tipo": tipo, **params}), loop)
        # sem conexão ativa: pedido permanece 'pendente' e é entregue quando a extensão conectar
        return pedido_id

    def _receber_resposta(self, resposta: dict) -> None:
        try:
            pedido_id = int(resposta.get("id"))
        except (TypeError, ValueError):
            return
        estado = "atendida" if resposta.get("ok") else "negada"
        db.atualizar(
            "pedido_extensao",
            pedido_id,
            {
                "estado": estado,
                "resposta_json": db.para_json(resposta.get("dados") or {"erro": resposta.get("erro", "")}),
                "decidida_em": db.agora(),
            },
        )
        futuro = self._futures.pop(pedido_id, None)
        if futuro is not None and not futuro.done():
            futuro.set_result(estado)

    # ---------- espera síncrona (usada pelo motor em threads) ----------
    def aguardar_resposta(self, pedido_id: int, timeout_s: float = 120.0, intervalo_s: float = 0.5) -> dict | None:
        """Bloqueia a thread chamante até a extensão responder ou expirar."""
        import time

        prazo = time.monotonic() + timeout_s
        while time.monotonic() < prazo:
            registro = db.buscar_um("pedido_extensao", pedido_id)
            if registro is None:
                return None
            if registro["estado"] != "pendente":
                return db.de_json(registro["resposta_json"], {})
            time.sleep(intervalo_s)
        db.atualizar("pedido_extensao", pedido_id, {"estado": "expirada", "decidida_em": db.agora()})
        return None


# Instância única do processo
ponte_navegador = BrowserBridge()
