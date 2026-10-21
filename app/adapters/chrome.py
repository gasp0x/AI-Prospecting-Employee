"""Gmail via Google Chrome logado: envio com compose pré-preenchido + Cmd+Enter.

Fluxo:
1. Abre a URL de composição do Gmail (view=cm) já preenchida no Chrome
   logado do usuário — a conta usada é a que está com sessão ativa;
2. Aciona Cmd+Enter (atalho de envio do Gmail);
3. Confirma pelo fechamento da janela do compose (contagem de janelas via
   Acessibilidade — sem ler conteúdo).

Requisitos: Chrome instalado + Gmail logado + atalhos de teclado do Gmail
habilitados (padrão do Gmail). Requer Acessibilidade para o host.
"""
from __future__ import annotations

import subprocess
import time
from urllib.parse import quote

from .. import db


def _janelas_chrome() -> list[str] | None:
    """Nomes das janelas do Google Chrome (None se não der para ler)."""
    try:
        processo = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to tell process "Google Chrome" to get name of every window'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        saida = (processo.stdout or "").strip()
        if not saida or "assistive" in saida.lower() or "erro" in saida.lower():
            return None
        return [parte.strip() for parte in saida.split(",") if parte.strip()]
    except (subprocess.SubprocessError, OSError):
        return None


def gmail_enviar(para: str, assunto: str, corpo: str) -> dict:
    """Abre o compose do Gmail preenchido no Chrome logado e envia (Cmd+Enter)."""
    url = (
        "https://mail.google.com/mail/?view=cm&fs=1"
        f"&to={quote(para)}&su={quote(assunto)}&body={quote(corpo)}"
    )
    antes = _janelas_chrome() or []

    subprocess.run(["open", "-a", "Google Chrome", url], check=True, timeout=15)

    # espera o compose abrir (a janela do compose entra na contagem)
    abriu = False
    for _ in range(15):
        time.sleep(1)
        agora = _janelas_chrome() or []
        if len(agora) > len(antes):
            abriu = True
            break
    if not abriu:
        # a leitura de janelas pode não refletir; tenta enviar mesmo assim
        pass

    # Cmd+Enter = enviar no Gmail
    processo = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke return using {command down}'],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )

    # confirma: a janela do compose fecha após o envio
    confirmado = False
    for _ in range(12):
        time.sleep(1)
        janelas = _janelas_chrome()
        if janelas is None:
            continue
        if len(janelas) <= len(antes):
            confirmado = True
            break

    db.registrar_auditoria("agente", "chrome_gmail_enviar", {
        "para": para, "assunto": assunto, "confirmado_fechamento": confirmado,
    })
    return {
        "para": para,
        "assunto": assunto,
        "enviado_via": "Gmail (Chrome logado)",
        "confirmado_fechamento": confirmado,
    }
