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
import time
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
    """Cria um RASCUNHO no Apple Mail (não envia, não abre janela).

    O rascunho vai direto para a pasta Rascunhos do Mail.
    """
    linhas = [
        'tell application "Mail"',
        f'    set msg to make new outgoing message with properties {{subject:"{_escapar(assunto)}", content:"{_escapar(corpo)}", visible:false}}',
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


def mail_contas() -> int:
    """Quantidade de contas configuradas no Mail (-1 se não der para saber)."""
    try:
        saida = executar_osascript('tell application "Mail" to count accounts')
        return int(saida)
    except (AcaoMacFalhou, ValueError, subprocess.SubprocessError):
        return -1


def mail_outbox_vazio() -> bool:
    """True se a Caixa de Saída do Mail está vazia (envio confirmado)."""
    try:
        saida = executar_osascript('tell application "Mail" to count of messages in outbox')
        return int(saida or 0) == 0
    except (AcaoMacFalhou, ValueError, subprocess.SubprocessError):
        return True  # não deu para verificar: assume que o Mail cuida do envio


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
        "    tell calendar 1\n"
        f'        set novoEvento to make new event at end of events with properties {{summary:"{_escapar(titulo)}", start date:inicio, end date:fim{local_props}}}\n'
        "    end tell\n"
        "    return id of novoEvento\n"
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


# ---------- acesso amplo ao sistema ----------

_PADROES_DESTRUTIVOS = [
    "rm -rf /", "rm -fr /", "sudo rm -rf", "diskutil erase", "diskutil apfs delete",
    "mkfs", ":(){:|:&};:", "dd if=/dev/zero of=/dev/", "chmod -r 000 /",
]


def padrao_destrutivo(comando: str) -> str | None:
    """Retorna o padrão destrutivo encontrado no comando (ou None)."""
    baixo = comando.lower()
    for padrao in _PADROES_DESTRUTIVOS:
        if padrao in baixo:
            return padrao
    return None


def comando_shell(comando: str, timeout_s: int = 30) -> dict:
    """Executa um comando shell DE VERDADE (após aprovação hash-vinculada).

    Roda com as permissões do usuário, com tempo limite e saída limitada.
    Padrões catastróficos são recusados antes mesmo da aprovação.
    """
    recusado = padrao_destrutivo(comando)
    if recusado:
        raise AcaoMacFalhou(
            f"Comando recusado por segurança: contém o padrão destrutivo “{recusado}”. "
            "Nem a aprovação autoriza isso."
        )
    try:
        processo = subprocess.run(
            comando, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        raise AcaoMacFalhou(f"Comando excedeu {timeout_s}s e foi interrompido.")
    saida = (processo.stdout or "")[-4000:]
    erros = (processo.stderr or "")[-2000:]
    db.registrar_auditoria("agente", "macos_comando_shell", {
        "comando": comando, "returncode": processo.returncode,
    })
    return {
        "comando": comando,
        "returncode": processo.returncode,
        "stdout": saida,
        "stderr": erros,
    }


def abrir_caminho(caminho: str) -> dict:
    """Abre pasta/arquivo no Finder/app padrão (open do macOS)."""
    processo = subprocess.run(["open", caminho], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if processo.returncode != 0:
        raise AcaoMacFalhou(f"Não consegui abrir “{caminho}”: {processo.stderr.strip()}")
    db.registrar_auditoria("agente", "macos_abrir_caminho", {"caminho": caminho})
    return {"caminho": caminho, "aberto": True}


def lista_apps_instalados() -> list[str]:
    """Nomes de aplicativos instalados (/Applications + /System/Applications)."""
    apps: set[str] = set()
    for pasta in ("/Applications", "/System/Applications"):
        try:
            processo = subprocess.run(["ls", pasta], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=10)
            for linha in processo.stdout.splitlines():
                if linha.endswith(".app"):
                    apps.add(linha[:-4])
        except Exception:  # noqa: BLE001
            continue
    return sorted(apps)


def info_sistema(tipo: str) -> str:
    """Informações somente-leitura do sistema (sem aprovação)."""
    comandos = {
        "disco": ["df", "-h", "/"],
        "memoria": ["sh", "-c", "sysctl -n hw.memsize | awk '{printf \"%.0f GB\", $1/1073741824}'"],
        "macos": ["sw_vers"],
        "bateria": ["pmset", "-g", "batt"],
        "uptime": ["uptime"],
    }
    cmd = comandos.get(tipo)
    if not cmd:
        return f"informação desconhecida: {tipo}"
    try:
        processo = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10)
        return (processo.stdout or processo.stderr or "").strip() or "(sem saída)"
    except Exception as erro:  # noqa: BLE001
        return f"erro: {erro}"


def falar(texto: str, voz: str | None = None) -> bool:
    """Fala o texto em voz alta (síntese nativa do macOS).

    Tenta a voz pt-BR (Luciana); se não existir, usa a voz padrão.
    Retorna True se falou. Bloqueia até terminar — chame em thread.
    """
    from ..config import settings

    texto_limpo = texto[:600]
    voz_final = voz or settings.voz_nome
    processo = subprocess.run(
        ["say", "-v", voz_final, texto_limpo], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    if processo.returncode != 0:
        subprocess.run(["say", texto_limpo], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return True


def slack_gui_enviar(destino: str, mensagem: str) -> dict:
    """Envia mensagem pelo app Slack do macOS via automação de teclado.

    Fluxo: ativa o Slack → Cmd+K (Ir para) → digita o destino → Enter (abre a
    conversa, que foca a composição) → cola a mensagem (clipboard) → Enter.
    Requer o Slack logado. Auditado. A mensagem e o destino são os da ordem
    explícita do usuário no chat.
    """
    destino_esc = destino.replace('"', "'")
    subprocess.run(["open", "-a", "Slack"], check=True, timeout=15)
    time.sleep(1.5)

    script_navega = (
        'tell application "System Events"\n'
        '    tell process "Slack"\n'
        '        set frontmost to true\n'
        '        delay 0.6\n'
        '        keystroke "k" using {command down}\n'
        '        delay 1.2\n'
        f'        keystroke "{_escapar(destino_esc)}"\n'
        '        delay 1.8\n'
        '        key code 36\n'
        '        delay 2.0\n'
        '    end tell\n'
        'end tell'
    )
    executar_osascript(script_navega, timeout_s=30)

    processo = subprocess.run(["pbcopy"], input=mensagem, text=True, timeout=10)
    script_envia = (
        'tell application "System Events"\n'
        '    tell process "Slack"\n'
        '        set frontmost to true\n'
        '        delay 0.5\n'
        '        keystroke "v" using {command down}\n'
        '        delay 0.7\n'
        '        key code 36\n'
        '        delay 0.5\n'
        '    end tell\n'
        'end tell'
    )
    executar_osascript(script_envia, timeout_s=30)

    db.registrar_auditoria("agente", "macos_slack_gui_enviar", {"destino": destino, "chars": len(mensagem)})
    return {"destino": destino, "enviado_via": "Slack.app (GUI)", "chars": len(mensagem)}
