"""Aprovações explícitas vinculadas à ação e ao conteúdo exatos.

Regra do produto: toda ação externa relevante (enviar mensagem, publicar,
convidar, excluir — e também ações locais que gravam em apps do usuário)
exige aprovação humana ANTES da execução.

Vínculo forte: a aprovação armazena o conteúdo integral e o SHA-256 dele.
Na execução, o conteúdo é re-verificado (hash deve bater) — qualquer edição
após a aprovação invalida a execução e exige nova aprovação.

Estados: pendente → aprovada → executada  |  pendente → negada
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from .. import db
from ..adapters import macos_apps
from .tasks import gerenciador_tarefas


def _hash(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def solicitar(
    tipo_acao: str,
    resumo: str,
    conteudo: str,
    params: dict | None = None,
    tarefa_id: int | None = None,
) -> int:
    """Registra uma aprovação pendente com o conteúdo exato a ser executado."""
    aprovacao_id = db.inserir(
        "aprovacao",
        {
            "tipo_acao": tipo_acao,
            "tarefa_id": tarefa_id,
            "resumo": resumo,
            "conteudo": conteudo,
            "conteudo_sha256": _hash(conteudo),
            "params_json": db.para_json(params or {}),
            "estado": "pendente",
            "criado_em": db.agora(),
        },
    )
    db.registrar_auditoria("agente", "aprovacao_solicitada", {"aprovacao_id": aprovacao_id, "tipo_acao": tipo_acao})
    return aprovacao_id


def decidir(aprovacao_id: int, aprovar: bool) -> dict:
    """Registra a decisão humana. Se aprovada, cria e inicia a tarefa executora."""
    registro = db.buscar_um("aprovacao", aprovacao_id)
    if registro is None:
        raise ValueError("aprovação não encontrada")
    if registro["estado"] != "pendente":
        raise ValueError(f"aprovação já decidida (estado: {registro['estado']})")

    db.atualizar(
        "aprovacao",
        aprovacao_id,
        {"estado": "aprovada" if aprovar else "negada", "decidida_em": db.agora()},
    )
    db.registrar_auditoria(
        "usuario",
        "aprovacao_aprovada" if aprovar else "aprovacao_negada",
        {"aprovacao_id": aprovacao_id, "tipo_acao": registro["tipo_acao"], "sha256": registro["conteudo_sha256"]},
    )
    if not aprovar:
        return {"estado": "negada"}

    tarefa_id = gerenciador_tarefas.criar(
        tipo=f"executar:{registro['tipo_acao']}",
        params={"aprovacao_id": aprovacao_id},
        aprovacao_id=aprovacao_id,
    )
    gerenciador_tarefas.iniciar(tarefa_id, _executar_aprovada)
    return {"estado": "aprovada", "tarefa_id": tarefa_id}


def _executar_aprovada(ctx, params: dict) -> dict:
    """Executor das ações aprovadas: re-verifica o hash e executa a ação real."""
    aprovacao_id = int(params["aprovacao_id"])
    registro = db.buscar_um("aprovacao", aprovacao_id)
    if registro is None:
        raise ValueError("aprovação não encontrada")
    if registro["estado"] != "aprovada":
        raise ValueError(f"aprovação em estado inesperado: {registro['estado']}")

    conteudo = registro["conteudo"]
    if _hash(conteudo) != registro["conteudo_sha256"]:
        raise ValueError("conteúdo alterado após a aprovação — hash não confere; nova aprovação necessária")

    ctx.evento(f"Executando ação aprovada: {registro['tipo_acao']}.", etapa="execucao")
    tipo = registro["tipo_acao"]
    extras = db.de_json(registro["params_json"], {})

    if tipo == "criar_nota_notes":
        resultado = macos_apps.notes_criar_nota(extras.get("titulo", "Nota"), conteudo)
    elif tipo == "criar_rascunho_mail":
        resultado = macos_apps.mail_criar_rascunho(
            extras.get("destinatario") or None,
            extras.get("assunto", "Abordagem comercial"),
            conteudo,
        )
        rascunho_id = extras.get("rascunho_id")
        if rascunho_id:
            db.atualizar("rascunho", int(rascunho_id), {"aprovacao_id": aprovacao_id})
    elif tipo == "executar_comando_shell":
        resultado = macos_apps.comando_shell(conteudo, timeout_s=int(extras.get("timeout_s", 30)))
    elif tipo == "enviar_mensagem_slack":
        from ..adapters import slack as slack_mod

        resultado = slack_mod.enviar_para(extras.get("destino", ""), conteudo)
    elif tipo == "enviar_email_gmail_chrome":
        from ..adapters import chrome

        resultado = chrome.gmail_enviar(extras["destinatario"], extras["assunto"], conteudo)
    elif tipo == "criar_lembrete_reminders":
        quando = datetime.fromisoformat(extras["quando_utc"])
        resultado = macos_apps.reminders_criar(extras.get("titulo", "Lembrete"), quando, conteudo)
    elif tipo == "criar_evento_calendar":
        inicio = datetime.fromisoformat(extras["inicio_utc"])
        resultado = macos_apps.calendar_criar_evento(
            extras.get("titulo", "Evento"), inicio, int(extras.get("duracao_min", 60)),
            extras.get("local", ""),
        )
    elif tipo == "enviar_email_mail":
        destinatario = (extras.get("destinatario") or "").strip()
        if not destinatario:
            raise ValueError("envio real exige destinatário preenchido")
        resultado = macos_apps.mail_enviar_email(
            destinatario,
            extras.get("assunto", "Abordagem comercial"),
            conteudo,
        )
        rascunho_id = extras.get("rascunho_id")
        if rascunho_id:
            db.atualizar("rascunho", int(rascunho_id), {"aprovacao_id": aprovacao_id})
    else:
        raise ValueError(f"tipo de ação sem executor: {tipo}")

    db.atualizar(
        "aprovacao",
        aprovacao_id,
        {"estado": "executada", "executada_em": db.agora(), "resultado_json": db.para_json(resultado)},
    )
    db.registrar_auditoria("agente", "acao_executada", {"aprovacao_id": aprovacao_id, "tipo_acao": tipo, "resultado": resultado})
    ctx.evento(f"Ação executada com sucesso: {resultado}", etapa="execucao")
    return resultado
