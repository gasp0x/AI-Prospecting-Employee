"""Canal Telegram: converse com o funcionário pelo celular (modelo OpenClaw).

Opcional: só ativa se `AIPE_TELEGRAM_TOKEN` estiver definido no `.env`
(crie o bot com o @BotFather no Telegram). Usa long-polling — nada exposto,
nada de webhook, tudo conversando com o servidor local.

A conversa compartilha o mesmo cérebro e a mesma memória da interface web:
mesmo histórico, mesmas aprovações hash-vinculadas, mesmos limites.
"""
from __future__ import annotations

import logging
import threading

import httpx

from .. import db
from ..config import settings
from ..engine import chatbot

log = logging.getLogger(__name__)

_URL_BASE = "https://api.telegram.org/bot{token}/{metodo}"


class CanalTelegram:
    def __init__(self, token: str) -> None:
        self._token = token
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._ultimo_enviado = 0

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="canal-telegram", daemon=True)
        self._thread.start()
        log.info("Canal Telegram iniciado.")

    def parar(self) -> None:
        self._parar.set()

    # ---------- envio ----------
    def _enviar(self, chat_id: int | str, texto: str) -> None:
        try:
            httpx.post(
                _URL_BASE.format(token=self._token, metodo="sendMessage"),
                json={"chat_id": chat_id, "text": texto[:4000]},
                timeout=20,
            )
        except httpx.HTTPError as erro:
            log.warning("Falha ao enviar mensagem ao Telegram: %s", erro)

    # ---------- recepção (long-polling) ----------
    def _loop(self) -> None:
        offset = 0
        ultimo_chat_id: int | str | None = None
        # cursor inicial: só espelha mensagens geradas DEPOIS de o canal subir
        maximo = db.listar("chat_msg", where="papel = 'agente'", ordem="id DESC", limite=1)
        self._ultimo_enviado = int(maximo[0]["id"]) if maximo else 0
        while not self._parar.is_set():
            try:
                resposta = httpx.get(
                    _URL_BASE.format(token=self._token, metodo="getUpdates"),
                    params={"offset": offset + 1, "timeout": 25},
                    timeout=35,
                )
                resposta.raise_for_status()
                for update in resposta.json().get("result", []):
                    offset = max(offset, int(update.get("update_id", 0)))
                    mensagem = update.get("message") or {}
                    texto = (mensagem.get("text") or "").strip()
                    chat_id = (mensagem.get("chat") or {}).get("id")
                    if not texto or chat_id is None:
                        continue
                    ultimo_chat_id = chat_id
                    chatbot.registrar_usuario(texto, canal="telegram")
                    for resp in chatbot.responder(texto):
                        self._enviar(chat_id, resp.texto)
                # espelha notificações geradas na web (mesma conversa, mesmo cérebro)
                novas = db.listar(
                    "chat_msg",
                    where="papel = 'agente' AND id > ?",
                    parametros=(self._ultimo_enviado,),
                    ordem="id ASC",
                    limite=20,
                )
                if novas:
                    self._ultimo_enviado = int(novas[-1]["id"])
                    if ultimo_chat_id is not None:
                        for linha in novas:
                            self._enviar(ultimo_chat_id, linha["texto"])
            except Exception as erro:  # noqa: BLE001 — canal opcional: registra e continua
                log.warning("Canal Telegram: %s", erro)
                self._parar.wait(5)


_canal: CanalTelegram | None = None


def iniciar_se_configurado() -> CanalTelegram | None:
    global _canal
    if settings.token_telegram and _canal is None:
        _canal = CanalTelegram(settings.token_telegram)
        _canal.iniciar()
    return _canal
