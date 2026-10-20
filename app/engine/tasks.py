"""Motor de tarefas: estados, tempo limite, tentativas, cancelamento e histórico.

Cada tarefa roda em uma thread dedicada com um `TaskContext` que:
- grava eventos de histórico (`tarefa_evento`);
- publica progresso (0–100);
- checa cancelamento (`threading.Event`) e prazo (`check()`), interrompendo
  a execução com exceções específicas nos pontos seguros.

Estados: pendente → em_execucao → concluida | falha | cancelada | expirada
         (e `aguardando_aprovacao` quando a tarefa depende de aprovação humana)
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from .. import db

# Exceções de controle
class TarefaCancelada(Exception):
    pass


class TarefaExpirada(Exception):
    pass


ESTADOS = {"pendente", "em_execucao", "aguardando_aprovacao", "concluida", "falha", "cancelada", "expirada"}

ROTULOS_ESTADO = {
    "pendente": "Pendente",
    "em_execucao": "Em execução",
    "aguardando_aprovacao": "Aguardando aprovação",
    "concluida": "Concluída",
    "falha": "Falha",
    "cancelada": "Cancelada",
    "expirada": "Expirada (tempo limite)",
}


@dataclass
class TaskContext:
    tarefa_id: int
    prazo_monotônico: float
    _cancelar: threading.Event = field(default_factory=threading.Event, repr=False)

    # --- pontos de verificação chamados pelos executores entre passos ---
    def check(self) -> None:
        if self._cancelar.is_set():
            raise TarefaCancelada()
        if time.monotonic() > self.prazo_monotônico:
            raise TarefaExpirada()

    def cancelar(self) -> None:
        self._cancelar.set()

    @property
    def cancelada(self) -> bool:
        return self._cancelar.is_set()

    # --- registro de histórico e progresso ---
    def evento(self, mensagem: str, nivel: str = "info", etapa: str = "", dados: dict | None = None) -> None:
        db.inserir(
            "tarefa_evento",
            {
                "tarefa_id": self.tarefa_id,
                "ts": db.agora(),
                "nivel": nivel,
                "etapa": etapa,
                "mensagem": mensagem,
                "dados_json": db.para_json(dados) if dados else None,
            },
        )

    def progresso(self, valor: int) -> None:
        db.atualizar("tarefa", self.tarefa_id, {"progresso": max(0, min(100, int(valor)))})

    def definir_estado(self, estado: str) -> None:
        if estado not in ESTADOS:
            raise ValueError(f"estado inválido: {estado}")
        db.atualizar("tarefa", self.tarefa_id, {"estado": estado})


@dataclass
class TarefaHandle:
    contexto: TaskContext
    thread: threading.Thread


class TaskManager:
    """Registro em memória das tarefas em execução + persistência do ciclo de vida."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ativas: dict[int, TarefaHandle] = {}

    # ---------- criação ----------
    def criar(self, tipo: str, params: dict, icp_id: int | None = None, aprovacao_id: int | None = None) -> int:
        tarefa_id = db.inserir(
            "tarefa",
            {
                "tipo": tipo,
                "estado": "pendente",
                "icp_id": icp_id,
                "aprovacao_id": aprovacao_id,
                "params_json": db.para_json(params),
                "criado_em": db.agora(),
            },
        )
        return tarefa_id

    # ---------- execução ----------
    def iniciar(
        self,
        tarefa_id: int,
        executar: Callable[[TaskContext, dict], dict],
        timeout_s: int | None = None,
    ) -> None:
        from ..config import settings

        registro = db.buscar_um("tarefa", tarefa_id)
        if registro is None:
            raise ValueError(f"tarefa {tarefa_id} não existe")
        if registro["estado"] not in {"pendente", "aguardando_aprovacao"}:
            raise ValueError(f"tarefa {tarefa_id} não pode ser iniciada no estado {registro['estado']}")
        if tarefa_id in self._ativas:
            raise ValueError(f"tarefa {tarefa_id} já está em execução")

        prazo = time.monotonic() + (timeout_s or settings.timeout_tarefa_s)
        ctx = TaskContext(tarefa_id=tarefa_id, prazo_monotônico=prazo)

        def _rodar() -> None:
            params = db.de_json(registro["params_json"], {})
            db.atualizar("tarefa", tarefa_id, {"estado": "em_execucao", "iniciado_em": db.agora()})
            ctx.evento("Tarefa iniciada.", etapa="inicio")
            try:
                resultado = executar(ctx, params)
                db.atualizar(
                    "tarefa",
                    tarefa_id,
                    {
                        "estado": "concluida",
                        "progresso": 100,
                        "resultado_json": db.para_json(resultado or {}),
                        "concluido_em": db.agora(),
                    },
                )
                ctx.evento("Tarefa concluída.", etapa="fim")
            except TarefaCancelada:
                db.atualizar("tarefa", tarefa_id, {"estado": "cancelada", "concluido_em": db.agora()})
                ctx.evento("Tarefa cancelada a pedido do usuário.", nivel="aviso", etapa="fim")
            except TarefaExpirada:
                db.atualizar("tarefa", tarefa_id, {"estado": "expirada", "erro": "Tempo limite excedido.", "concluido_em": db.agora()})
                ctx.evento("Tarefa expirada: tempo limite excedido.", nivel="erro", etapa="fim")
            except Exception as erro:  # noqa: BLE001 — fronteira de thread: registra e marca falha
                db.atualizar(
                    "tarefa",
                    tarefa_id,
                    {"estado": "falha", "erro": f"{type(erro).__name__}: {erro}", "concluido_em": db.agora()},
                )
                ctx.evento(
                    f"Falha na tarefa: {type(erro).__name__}: {erro}",
                    nivel="erro",
                    etapa="fim",
                    dados={"traceback": traceback.format_exc(limit=5)},
                )
            finally:
                with self._lock:
                    self._ativas.pop(tarefa_id, None)

        thread = threading.Thread(target=_rodar, name=f"tarefa-{tarefa_id}", daemon=True)
        with self._lock:
            self._ativas[tarefa_id] = TarefaHandle(contexto=ctx, thread=thread)
        thread.start()

    # ---------- controle ----------
    def cancelar(self, tarefa_id: int) -> bool:
        """Sinaliza cancelamento. A tarefa para no próximo ponto de verificação."""
        registro = db.buscar_um("tarefa", tarefa_id)
        if registro is None or registro["estado"] not in {"pendente", "em_execucao"}:
            return False
        handle = self._ativas.get(tarefa_id)
        if handle is not None:
            handle.contexto.cancelar()
            return True
        # pendente e ainda não iniciada em thread: cancela direto no banco
        db.atualizar("tarefa", tarefa_id, {"estado": "cancelada", "concluido_em": db.agora()})
        db.inserir(
            "tarefa_evento",
            {"tarefa_id": tarefa_id, "ts": db.agora(), "nivel": "aviso", "etapa": "fim", "mensagem": "Tarefa cancelada antes de iniciar."},
        )
        return True

    def aguardar_todas(self, timeout_s: float = 10.0) -> None:
        """Bloqueia até todas as threads de tarefas terminarem (útil em testes/desligamento)."""
        prazo = time.monotonic() + timeout_s
        while time.monotonic() < prazo:
            with self._lock:
                handles = list(self._ativas.values())
            if not handles:
                return
            for handle in handles:
                handle.thread.join(timeout=0.5)

    def contexto(self, tarefa_id: int) -> TaskContext | None:
        handle = self._ativas.get(tarefa_id)
        return handle.contexto if handle else None


# Instância única do processo (monólito modular: os módulos importam este objeto)
gerenciador_tarefas = TaskManager()
