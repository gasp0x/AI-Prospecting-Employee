"""Adaptador de aplicativos locais do macOS via AppleScript (`osascript`).

Ações suportadas no MVP:
- criar nota no Apple Notes (demo da ação local autorizada);
- criar RASCUNHO de e-mail no Apple Mail (nunca envia automaticamente).

Permissões: a primeira execução dispara o diálogo do macOS (TCC: "…deseja
controlar Notes/Mail"). Se o usuário negar, `osascript` falha com erro -1743,
que traduzimos para uma mensagem clara de como liberar em
Ajustes do Sistema → Privacidade e Segurança → Automação.

Segurança: cada chamada é auditada; strings são escapadas para AppleScript;
nenhum comando é montado com entrada do usuário sem escape.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime

from .. import db

class AcaoMacFalhou(Exception):
    pass


class PermissaoNegada(AcaoMacFalhou):
    pass


def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace('"', '\\"')


def executar_osascript(script: str, timeout_s: int = 25) -> str:
    try:
        processo = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as erro:
        raise AcaoMacFalhou(f"osascript excedeu o tempo limite ({timeout_s}s): {erro}") from erro
    if processo.returncode != 0:
        saida = (processo.stderr or "").strip()
        if "-1743" in saida or "not authorized" in saida.lower():
            raise PermissaoNegada(
                "Permissão negada pelo macOS (TCC). Libere em Ajustes do Sistema → "
                "Privacidade e Segurança → Automação → permita controlar o app para o "
                "terminal/aplicativo que executa este projeto."
            )
        raise AcaoMacFalhou(f"osascript falhou: {saida or processo.returncode}")
    return (processo.stdout or "").strip()


def notes_criar_nota(titulo: str, corpo: str) -> dict:
    """Cria uma nota no Apple Notes. Retorna {id, titulo}."""
    corpo_html = "<div>" + "</div><div>".join(_escapar(linha) for linha in corpo.splitlines() or [""]) + "</div>"
    script = (
        'tell application "Notes"\n'
        f'    set novaNota to make new note with properties {{name:"{_escapar(titulo)}", body:"{corpo_html}"}}\n'
        "    return id of novaNota\n"
        "end tell"
    )
    resultado = executar_osascript(script)
    db.registrar_auditoria("agente", "macos_notes_criar_nota", {"titulo": titulo, "id_nota": resultado})
    return {"id": resultado, "titulo": titulo}


def mail_criar_rascunho(para: str | None, assunto: str, corpo: str) -> dict:
    """Cria um RASCUNHO no Apple Mail (não envia). Retorna {id, assunto}."""
    linhas = [
        'tell application "Mail"',
        f'    set msg to make new outgoing message with properties {{subject:"{_escapar(assunto)}", content:"{_escapar(corpo)}", visible:true}}',
    ]
    if para:
        linhas.append(
            f'    make new to recipient at end of to recipients of msg with properties {{address:"{_escapar(para)}"}}'
        )
    linhas += ["    return id of msg", "end tell"]
    resultado = executar_osascript("\n".join(linhas))
    db.registrar_auditoria(
        "agente", "macos_mail_criar_rascunho", {"assunto": assunto, "para": para or "(sem destinatário)", "id_mensagem": resultado}
    )
    return {"id": resultado, "assunto": assunto}


def mail_enviar_email(para: str, assunto: str, corpo: str) -> dict:
    """ENVIA um e-mail de verdade pelo Apple Mail.

    Só pode ser chamada pelo executor de aprovações (`app.engine.approvals`) —
    a aprovação com hash do conteúdo exato é a barreira obrigatória.
    """
    script = (
        'tell application "Mail"\n'
        f'    set msg to make new outgoing message with properties {{subject:"{_escapar(assunto)}", content:"{_escapar(corpo)}", visible:false}}\n'
        f'    make new to recipient at end of to recipients of msg with properties {{address:"{_escapar(para)}"}}\n'
        "    send msg\n"
        "    return id of msg\n"
        "end tell"
    )
    resultado = executar_osascript(script, timeout_s=40)
    db.registrar_auditoria("agente", "macos_mail_enviar_email", {"assunto": assunto, "para": para, "id_mensagem": resultado})
    return {"id": resultado, "assunto": assunto, "para": para, "enviado": True}


def _data_apple(quando: datetime) -> str:
    """Delta em minutos a partir de agora — evita literais de data sensíveis a locale."""
    delta_min = round((quando - datetime.now()).total_seconds() / 60)
    if delta_min < 1:
        delta_min = 1
    return f"((current date) + {delta_min} * minutes)"


def reminders_criar(titulo: str, quando: datetime, corpo: str = "") -> dict:
    """Cria um lembrete no app Lembretes (Reminders) para o horário indicado."""
    corpo_props = f', body:"{_escapar(corpo)}"' if corpo else ""
    script = (
        'tell application "Reminders"\n'
        f'    make new reminder with properties {{name:"{_escapar(titulo)}"{corpo_props}, due date:{_data_apple(quando)}}}\n'
        "    return name of it\n"
        "end tell"
    )
    resultado = executar_osascript(script)
    db.registrar_auditoria("agente", "macos_reminders_criar", {"titulo": titulo, "quando": quando.isoformat()})
    return {"titulo": titulo, "quando": quando.isoformat()}


def calendar_criar_evento(titulo: str, inicio: datetime, duracao_min: int = 60, local: str = "") -> dict:
    """Cria um evento no Calendário do macOS (primeiro calendário gravável)."""
    delta_inicio = round((inicio - datetime.now()).total_seconds() / 60)
    if delta_inicio < 1:
        delta_inicio = 1
    local_props = f', location:"{_escapar(local)}"' if local else ""
    script = (
        'tell application "Calendar"\n'
        "    set inicio to (current date) + " + str(delta_inicio) + " * minutes\n"
        "    set fim to inicio + " + str(int(duracao_min)) + " * minutes\n"
        f'    make new event at end of events of calendar 1 with properties {{summary:"{_escapar(titulo)}", start date:inicio, end date:fim{local_props}}}\n'
        "    return summary of it\n"
        "end tell"
    )
    resultado = executar_osascript(script)
    db.registrar_auditoria("agente", "macos_calendar_criar_evento", {
        "titulo": titulo, "inicio": inicio.isoformat(), "duracao_min": duracao_min,
    })
    return {"titulo": titulo, "inicio": inicio.isoformat(), "duracao_min": duracao_min}


def extrair_id_resultado(saida: str) -> str:
    """Utilitário para limpar o identificador retornado pelo app."""
    return re.sub(r"\s+", " ", saida).strip()
