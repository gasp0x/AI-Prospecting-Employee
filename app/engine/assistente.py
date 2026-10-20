"""Camada de assistente geral: habilidades úteis que vão além da prospecção.

Filosofia: habilidades determinísticas funcionam SEMPRE (sem chave de API, sem
LLM) — cálculo, data/hora, cotações, clima, lembretes, resumo extrativo de
páginas, abrir sites e anotações no Apple Notes. Perguntas abertas ("quem foi
Machado de Assis?", "escreva um e-mail formal") são respondidas pelo LLM
opcional (`LLM_*` no `.env`); sem LLM, o assistente diz com honestidade o que
consegue fazer.

Fontes públicas usadas: AwesomeAPI (câmbio) e Open-Meteo (clima), ambas sem
chave. Nominatim é reaproveitado para geocodificar o clima.
"""
from __future__ import annotations

import ast
import re
import subprocess
import threading
import time
import unicodedata
import webbrowser
from datetime import datetime, timedelta, timezone

import httpx
from urllib.parse import urlparse

from .. import db
from ..adapters import brasilapi, macos_apps, nominatim, sites, websearch
from ..engine.evidence import extrair_entidades, extrair_pagina
from ..config import settings
from ..engine import approvals
from .mensagens import Mensagem, registrar_agente
from ..engine.llm import completar_texto, disponivel as llm_disponivel

_URL_CAMBIO = "https://economia.awesomeapi.com.br/last/USD-BRL,EUR-BRL"
_URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
_URL_NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower().strip()


# ---------- agendador de lembretes (thread única do processo) ----------

_agendador_iniciado = False
_agendador_lock = threading.Lock()


def iniciar_agendador() -> None:
    global _agendador_iniciado
    with _agendador_lock:
        if _agendador_iniciado:
            return
        _agendador_iniciado = True
    threading.Thread(target=_loop_lembretes, name="agendador-lembretes", daemon=True).start()


def _loop_lembretes() -> None:
    while True:
        try:
            verificar_lembretes()
        except Exception as erro:  # noqa: BLE001 — agendador nunca derruba o processo
            print(f"[assistente] agendador: {erro}")
        time.sleep(15)


def verificar_lembretes() -> list[db.sqlite3.Row]:
    """Entrega lembretes vencidos como mensagens do agente no chat."""
    agora = db.agora()
    vencidos = db.listar(
        "lembrete", where="estado = 'pendente' AND quando_utc <= ?", parametros=(agora,), ordem="id ASC"
    )
    for lembrete in vencidos:
        msg = Mensagem(f"⏰ **Lembrete:** {lembrete['texto']}")
        registrar_agente(msg)
        db.atualizar("lembrete", lembrete["id"], {"estado": "entregue", "entregue_em": db.agora()})
    return vencidos


# ---------- habilidades ----------



# ---------- abrir aplicativos do sistema (open -a do macOS) ----------

_APPS_CONHECIDOS = {
    "calendario": "Calendar", "calendar": "Calendar",
    "mail": "Mail", "email": "Mail",
    "lembretes": "Reminders", "reminders": "Reminders",
    "notas": "Notes", "notes": "Notes",
    "contatos": "Contacts", "contacts": "Contacts",
    "safari": "Safari",
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "edge": "Microsoft Edge", "firefox": "Firefox", "brave": "Brave Browser",
    "calculadora": "Calculator", "calculator": "Calculator",
    "mapas": "Maps", "maps": "Maps",
    "musica": "Music", "music": "Music",
    "fotos": "Photos", "photos": "Photos",
    "mensagens": "Messages", "messages": "Messages",
    "telegram": "Telegram", "whatsapp": "WhatsApp",
    "spotify": "Spotify",
    "terminal": "Terminal",
    "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
    "visualizador": "Preview", "preview": "Preview",
    "configuracoes": "System Settings", "system settings": "System Settings",
    "dicionario": "Dictionary",
    "lembretes app": "Reminders",
}


# ---------- acesso amplo ao sistema ----------

def _sistema_info(texto: str) -> list[Mensagem] | None:
    """Informações somente-leitura: disco, memória, macOS, bateria, uptime."""
    n = _norm(texto)
    tipos = []
    if re.search(r"\b(espac\w* em disco|disco)\b", n):
        tipos.append("disco")
    if re.search(r"\b(memoria|ram)\b", n):
        tipos.append("memoria")
    if re.search(r"\b(versao do macos|versao do sistema|macos|os x)\b", n):
        tipos.append("macos")
    if re.search(r"\bbateria\b", n):
        tipos.append("bateria")
    if re.search(r"\buptime|ligado ha\b", n):
        tipos.append("uptime")
    if not tipos:
        return None
    partes = [f"**{tipo.title()}:**\n{macos_apps.info_sistema(tipo)}" for tipo in tipos]
    return [Mensagem("\n\n".join(partes) + "\n\n(leitura somente — nada foi alterado)")]


_PADRAO_SHELL = re.compile(r"^\s*(?:rod[ae]|execut[ae]|terminal|shell)\s*[:;]?\s*(.+)$", re.IGNORECASE | re.DOTALL)


def _shell(texto: str) -> list[Mensagem] | None:
    """Comando shell DE VERDADE — sempre com aprovação do comando exato."""
    m = _PADRAO_SHELL.match(texto)
    if not m:
        return None
    comando = m.group(1).strip()
    if not comando:
        return None
    recusado = macos_apps.padrao_destrutivo(comando)
    if recusado:
        return [Mensagem(
            f"⛔ Comando recusado por segurança: contém o padrão destrutivo “{recusado}”. "
            "Nem a aprovação autoriza isso."
        )]
    try:
        aprovacao_id = approvals.solicitar(
            tipo_acao="executar_comando_shell",
            resumo="EXECUTAR comando no terminal do seu Mac (shell, permissões do usuário, 30s de limite).",
            conteudo=comando,
            params={"timeout_s": 30},
        )
    except Exception as erro:  # noqa: BLE001 — recusa de padrão destrutivo
        return [Mensagem(str(erro))]
    return [Mensagem(
        f"Comando pronto para **executar no terminal**:\n\n```\n{comando}\n```\n\n"
        f"Responda **aprovar {aprovacao_id}** para executar (hash registrado) · **negar {aprovacao_id}** cancela."
    )]


def _abrir_caminho(texto: str) -> list[Mensagem] | None:
    """Abrir pasta/arquivo: "abra a pasta Documentos", "abra ~/Desktop"."""
    m = re.search(r"\babr[aei]\s+(?:a\s+)?(?:pasta|o\s+arquivo|o\s+documento)\s+(\S.*)\s*$",
                  texto, re.IGNORECASE)
    if not m:
        return None
    alvo = m.group(1).strip()
    atalhos = {"documentos": "~/Documents", "documents": "~/Documents",
               "downloads": "~/Downloads", "desktop": "~/Desktop",
               "area de trabalho": "~/Desktop"}
    if alvo.lower() in atalhos:
        alvo = atalhos[alvo.lower()]
    try:
        resultado = macos_apps.abrir_caminho(alvo)
    except macos_apps.AcaoMacFalhou as erro:
        return [Mensagem(str(erro))]
    return [Mensagem(f"✅ Abri {resultado['caminho']} no Finder/app padrão.")]


def _lista_apps(texto: str) -> list[Mensagem] | None:
    if not re.search(r"\b(apps? instalados?|lista de apps?|aplicativos instalados?)\b", _norm(texto)):
        return None
    apps = macos_apps.lista_apps_instalados()
    if not apps:
        return [Mensagem("Não consegui listar os aplicativos.")]
    return [Mensagem(f"**{len(apps)} aplicativos instalados:**\n" + ", ".join(apps[:60]) +
                     ("…" if len(apps) > 60 else ""))]


def _abrir_app(texto: str) -> list[Mensagem] | None:
    """Abre um aplicativo do macOS: "abra o calendário", "abra o mail"…"""
    m = re.match(r"\s*abr[aei]r?\s+(.+)$", texto.strip(), re.IGNORECASE)
    if not m:
        return None
    resto = _norm(m.group(1))
    # remove artigos e "app"
    resto = re.sub(r"\b(o|a|os|as|meu|minha|app|aplicativo|programa)\b", " ", resto)
    resto = re.sub(r"\s+", " ", resto).strip()
    # corta justificativas: "abra o calendário pra eu agendar…" → "calendario"
    resto = re.split(r"\b(pra|para|poder|que|e depois|assim)\b", resto)[0].strip()
    app = _APPS_CONHECIDOS.get(resto)
    if not app:
        for instalado in macos_apps.lista_apps_instalados():
            if _norm(instalado) == resto or resto in _norm(instalado):
                app = instalado
                break
    if not app:
        return None
    try:
        subprocess.run(["open", "-a", app], check=True, timeout=15)
    except (subprocess.SubprocessError, OSError) as erro:
        return [Mensagem(f"Não consegui abrir o {app}: {erro}")]
    dicas = {
        "Calendar": "Para eu mesmo agendar: \"agende reunião com X amanhã às 14h\".",
        "Reminders": "Para eu criar lembretes: \"lembre-me no Lembretes de pagar o boleto amanhã às 9h\".",
        "Mail": "Para eu enviar: \"envie e-mail para foo@bar.com com assunto X dizendo: corpo\".",
    }
    dica = dicas.get(app)
    extra = f"\n\n{dica}" if dica else ""
    return [Mensagem(f"✅ Abri o **{app}**.{extra}")]



def tentar(texto: str) -> list[Mensagem] | None:
    """Tenta as habilidades determinísticas; None = não é comando conhecido."""
    for handler in (_abrir_app, _lista_apps, _sistema_info, _shell, _abrir_caminho, _hora_data, _calculo, _cotacao, _clima, _lembrete_sistema, _agenda,
                    _email_chat, _lembrete_criar, _lembrete_listar, lista_nomes, _buscar_info,
                    _resumir_url, _abrir_site, _anotar):
        try:
            resposta = handler(texto)
        except Exception as erro:  # noqa: BLE001 — habilidade falhou; tenta a próxima
            resposta = [Mensagem(f"Deu erro nessa habilidade ({type(erro).__name__}: {erro}). Tente reformular.")]
        if resposta is not None:
            return resposta
    return None


def _livre(texto: str) -> list[Mensagem]:
    """Pergunta aberta via LLM opcional; sem LLM, responde com honestidade."""
    if llm_disponivel():
        resposta = completar_texto(
            "Você é um assistente pessoal em português do Brasil, direto e útil. "
            "Responda em até 200 palavras. Se não souber, diga que não sabe.",
            texto,
        )
        if resposta:
            return [Mensagem(resposta)]
    return [Mensagem(
        "Para perguntas abertas como essa, eu preciso de um LLM configurado: defina "
        "`LLM_BASE_URL`, `LLM_API_KEY` e `LLM_MODELO` no `.env` (compatível com OpenAI) e reinicie.\n\n"
        "Sem LLM, eu já faço bastante coisa — veja \"ajuda\": prospecção completa, cálculos, "
        "cotações, clima, lembretes, resumo de páginas e anotações no Notes."
    )]


def _hora_data(texto: str) -> list[Mensagem] | None:
    n = _norm(texto)
    if not re.search(r"\b(que horas|hora agora|que dia|data de hoje|hoje e qual dia)\b", n):
        return None
    agora = datetime.now().astimezone()
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return [Mensagem(
        f"Agora são {agora.strftime('%H:%M')} de {dias[agora.weekday()]}-feira, "
        f"{agora.strftime('%d/%m/%Y')} (horário local do seu Mac)." if agora.weekday() < 5 else
        f"Agora são {agora.strftime('%H:%M')} de {dias[agora.weekday()]}, "
        f"{agora.strftime('%d/%m/%Y')} (horário local do seu Mac)."
    )]


def _avaliar_expressao(expr: str) -> str | None:
    expr = expr.strip().replace(",", ".")
    # "15% de 240" → (15/100)*240 (antes do whitelist, pois contém letras)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%\s*de\s*(\d+(?:\.\d+)?)", expr)
    if m:
        r = round(float(m.group(1)) / 100 * float(m.group(2)), 4)
        return str(int(r) if float(r).is_integer() else r)
    permitido = re.compile(r"^[\d\s+\-*/().%]+$")
    if not permitido.match(expr):
        return None
    expr = expr.replace("%", "/100")
    try:
        arvore = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(arvore):
        if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                 ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
                                 ast.USub, ast.UAdd)):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                continue
            return None
    try:
        resultado = eval(compile(arvore, "<calc>", "eval"))  # noqa: S307 — AST whitelisted acima
    except (ZeroDivisionError, SyntaxError, OverflowError):
        return None
    if isinstance(resultado, float) and resultado.is_integer():
        resultado = int(resultado)
    return str(resultado)


def _calculo(texto: str) -> list[Mensagem] | None:
    n = texto
    m = re.search(r"(?:calcul[ae]|quanto (?:é|e|da|dah)|resolva)\s+(.+)", n, re.IGNORECASE)
    expr = m.group(1).strip().rstrip("?!.") if m else None
    if not expr and re.fullmatch(r"[\d\s+\-*/().,%]+", n or "") and any(op in n for op in "+-*/%"):
        expr = n
    if not expr:
        return None
    resultado = _avaliar_expressao(expr)
    if resultado is None:
        return None
    return [Mensagem(f"{expr.strip()} = **{resultado}**")]


def _cotacao(texto: str) -> list[Mensagem] | None:
    n = _norm(texto)
    if not re.search(r"\b(cotacao|cotado|dolar|dollar|euro|btc|bitcoin)\b", n):
        return None
    resposta = httpx.get(_URL_CAMBIO, timeout=15,
                         headers={"User-Agent": settings.agente_usuario})
    resposta.raise_for_status()
    dados = resposta.json()
    linhas = []
    rotulos = {"USD": "Dólar", "EUR": "Euro"}
    for codigo, info in dados.items():
        moeda = rotulos.get(codigo.replace("BRL", "").strip(), codigo)
        linhas.append(f"· {moeda}: **R$ {float(info['bid']):.4f}** ({info.get('create_date', '')[:10]})")
    return [Mensagem("Cotações de hoje:\n" + "\n".join(linhas))]


_CLIMA_CODIGOS = {0: "céu limpo", 1: "predominantemente limpo", 2: "parcialmente nublado", 3: "nublado",
                  45: "neblina", 48: "neblina com geada", 51: "garoa leve", 53: "garoa", 55: "garoa forte",
                  61: "chuva leve", 63: "chuva", 65: "chuva forte", 71: "neve leve", 80: "pancadas de chuva",
                  81: "pancadas fortes", 95: "tempestade", 96: "tempestade com granizo"}


def _clima(texto: str) -> list[Mensagem] | None:
    m = re.search(r"\b(?:clima|tempo)\s+(?:em|de|no|na)\s+(.{2,60})$", texto, re.IGNORECASE)
    if not m:
        return None
    cidade = m.group(1).strip().rstrip("?!. ")
    geo = httpx.get(
        _URL_NOMINATIM,
        params={"q": cidade, "format": "jsonv2", "limit": 1, "countrycodes": "br"},
        headers={"User-Agent": settings.agente_usuario},
        timeout=15,
    )
    lugares = geo.json()
    if not lugares:
        return [Mensagem(f"Não encontrei a cidade “{cidade}”.")]
    lat, lon = lugares[0]["lat"], lugares[0]["lon"]
    clima = httpx.get(
        _URL_OPEN_METEO,
        params={"latitude": lat, "longitude": lon, "current_weather": "true"},
        headers={"User-Agent": settings.agente_usuario},
        timeout=15,
    )
    atual = clima.json().get("current_weather", {})
    codigo = int(atual.get("weathercode", -1))
    descricao = _CLIMA_CODIGOS.get(codigo, "condição não mapeada")
    return [Mensagem(
        f"Clima em {lugares[0].get('name', cidade)}: **{atual.get('temperature', '?')}°C**, "
        f"{descricao}, vento {atual.get('windspeed', '?')} km/h (Open-Meteo, agora)."
    )]


def _lembrete_criar(texto: str) -> list[Mensagem] | None:
    m = re.search(
        r"\blembre?(?:-| )?me\s+(?:de\s+)?(.+?)(?:\s+(?:daqui a|em)\s+(\d+)\s*(minutos?|min|horas?|h|segundos?|s))"
        r"?(\s+amanh[ãa])?(\s+hoje)?(?:\s+[àa]s\s+(\d{1,2})[:h](\d{2}))?\s*$",
        texto, re.IGNORECASE,
    )
    if not m or not re.search(r"\blembre\b", _norm(texto)):
        return None
    conteudo = m.group(1).strip()
    agora = datetime.now(timezone.utc)
    quando = agora
    if m.group(2):
        quantidade = int(m.group(2))
        unidade = m.group(3).lower()
        segundos = {"s": 1, "segundos": 1, "min": 60, "minutos": 60, "h": 3600, "horas": 3600}
        quando = agora + timedelta(seconds=quantidade * segundos.get(unidade, 60))
    elif m.group(6):
        alvo = agora.replace(hour=int(m.group(6)), minute=int(m.group(7)), second=0, microsecond=0)
        if alvo <= agora:
            alvo += timedelta(days=1)
        quando = alvo
    elif m.group(4):
        alvo = agora.replace(hour=9, minute=0, second=0, microsecond=0)
        quando = alvo if alvo > agora else alvo + timedelta(days=1)
    else:
        return [Mensagem("Quando devo lembrar? Ex.: \"lembre-me de ligar para o João em 30 minutos\" "
                         "ou \"lembre-me de enviar o relatório amanhã às 9h00\".")]
    db.inserir("lembrete", {
        "texto": conteudo, "quando_utc": quando.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "criado_em": db.agora(),
    })
    local = quando.astimezone()
    return [Mensagem(f"Ok ✓ Vou te lembrar de **{conteudo}** às {local.strftime('%d/%m %H:%M')} "
                     "(o aviso aparece neste chat).")]


def _lembrete_listar(texto: str) -> list[Mensagem] | None:
    if not re.search(r"\b(meus? lembretes?|lembretes ativos)\b", _norm(texto)):
        return None
    pendentes = db.listar("lembrete", where="estado = 'pendente'", ordem="quando_utc ASC")
    if not pendentes:
        return [Mensagem("Nenhum lembrete ativo.")]
    linhas = [f"· #{l['id']} {l['quando_utc']} — {l['texto']}" for l in pendentes]
    return [Mensagem("Lembretes ativos:\n" + "\n".join(linhas))]




_DIAS_SEMANA = {"segunda": 0, "terca": 1, "terça": 1, "quarta": 2, "quinta": 3,
                "sexta": 4, "sabado": 5, "sábado": 5, "domingo": 6}


def interpretar_quando(texto: str, base: datetime | None = None) -> tuple[datetime, str] | None:
    """Extrai data/hora em pt-BR. Retorna (momento, texto_restante) ou None.

    Aceita: "amanhã às 14h", "hoje 15:30", "dia 25/10 às 9h", "25/10/2026 14:30",
    "em 30 minutos", "em 2 horas", "sexta às 10h", "próxima segunda às 9h".
    """
    base = base or datetime.now()

    # 1) hora primeiro (e sai do texto restante para não bagunçar as posições)
    hora = None
    restante = texto
    m_hora = (re.search(r"\b(?:[àa]s\s*)?(\d{1,2})[:h](\d{2})\b", restante, re.IGNORECASE)
              or re.search(r"\b(?:[àa]s\s*)?(\d{1,2})\s*h(?:oras?)?\b", restante, re.IGNORECASE))
    if m_hora:
        hora = (int(m_hora.group(1)), int(m_hora.group(2)) if m_hora.lastindex and m_hora.lastindex > 1 and m_hora.group(2) else 0)
        restante = (restante[:m_hora.start()] + " " + restante[m_hora.end():]).strip()

    # 2) data base
    quando = None
    m_rel = re.search(r"\bem\s+(\d+)\s*(minutos?|min|horas?|h|dias?|semanas?)\b", restante, re.IGNORECASE)
    m_data = re.search(r"\b(?:dia\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", restante)
    m_amanha = re.search(r"\bamanh[ãa]\b", restante, re.IGNORECASE)
    m_dia_semana = re.search(
        r"\b(?:pr[óo]xim[ao]\s+|essa\s+|esta\s+)?(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)(?:-feira)?\b",
        restante, re.IGNORECASE,
    )
    m_hoje = re.search(r"\bhoje\b", restante, re.IGNORECASE)

    if m_rel:
        quantidade = int(m_rel.group(1))
        unidade = m_rel.group(2).lower()
        quando = base + timedelta(
            minutes=quantidade * {"min": 1, "minutos": 1, "h": 60, "horas": 60,
                                  "dias": 1440, "dia": 1440, "semanas": 10080}.get(unidade, 60)
        )
        restante = (restante[:m_rel.start()] + " " + restante[m_rel.end():]).strip()
    elif m_data:
        dia, mes = int(m_data.group(1)), int(m_data.group(2))
        ano = int(m_data.group(3)) if m_data.group(3) else base.year
        quando = base.replace(year=ano, month=mes, day=dia, hour=9, minute=0, second=0, microsecond=0)
        if quando < base:
            quando = quando.replace(year=ano + 1)
        restante = (restante[:m_data.start()] + " " + restante[m_data.end():]).strip()
    elif m_amanha:
        quando = (base + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        restante = re.sub(r"\bamanh[ãa]\b", " ", restante, flags=re.IGNORECASE).strip()
    elif m_dia_semana:
        nome = _norm(m_dia_semana.group(1))
        alvo = _DIAS_SEMANA.get(nome, 0)
        dias_ahead = (alvo - base.weekday()) % 7
        if dias_ahead == 0:
            dias_ahead = 7
        quando = (base + timedelta(days=dias_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
        restante = re.sub(
            r"\b(?:pr[óo]xim[ao]\s+|essa\s+|esta\s+)?(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)(?:-feira)?\b",
            " ", restante, flags=re.IGNORECASE,
        ).strip()
    elif m_hoje:
        quando = base
        restante = re.sub(r"\bhoje\b", " ", restante, flags=re.IGNORECASE).strip()
    else:
        return None

    if hora:
        h, mnt = hora
        quando = quando.replace(hour=min(h, 23), minute=min(mnt, 59), second=0, microsecond=0)
    return quando, re.sub(r"\s+", " ", restante).strip(" ,.")


def _lembrete_sistema(texto: str) -> list[Mensagem] | None:
    """Lembrete no app Lembretes do macOS (com aprovação)."""
    n = _norm(texto)
    if not re.search(r"\b(no lembretes|no reminders|no app de lembretes|lembretes do (mac|sistema))\b", n):
        return None
    extraido = interpretar_quando(texto)
    if extraido is None:
        return [Mensagem("Quando? Ex.: \"lembre-me no Lembretes de pagar o boleto amanhã às 9h\".")]
    quando, restante = extraido
    titulo = re.sub(r"\b(lembr(?:e|a)(?:-| )?me|no lembretes|no reminders|no app de lembretes|lembretes do (mac|sistema)|de|para)\b", " ", restante, flags=re.IGNORECASE).strip(" ,.") or "Lembrete"
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_lembrete_reminders",
        resumo=f"Criar lembrete no app Lembretes para {quando.astimezone().strftime('%d/%m/%Y %H:%M')}.",
        conteudo=titulo,
        params={"titulo": titulo, "quando_utc": quando.isoformat()},
    )
    return [Mensagem(
        f"Lembrete pronto para o app **Lembretes**:\n· {titulo}\n· Para: {quando.astimezone().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Responda **aprovar {aprovacao_id}** para gravar (hash registrado)."
    )]


def _agenda(texto: str) -> list[Mensagem] | None:
    """Evento no Calendário do macOS (com aprovação)."""
    n = _norm(texto)
    if not re.search(r"\b(agende|marqu?e|marcar|no calendario|calendario|evento|reuniao)\b", n):
        return None
    if not re.search(r"\b(agende|marqu?e|marcar)\b", n) and "calendario" not in n:
        return None
    extraido = interpretar_quando(texto)
    if extraido is None:
        return [Mensagem("Quando? Ex.: \"agende reunião com o cliente amanhã às 14h\" "
                         "ou \"marque no calendário dentista dia 25/10 às 9h\".")]
    quando, restante = extraido
    titulo = re.sub(
        r"\b(agende|marqu?e|marcar|no calendario|calendario|um|uma)\b", " ",
        restante, flags=re.IGNORECASE,
    )
    titulo = re.sub(r"\s+", " ", titulo).strip(" ,.") or "Evento"
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_evento_calendar",
        resumo=f"Criar evento no Calendário: “{titulo}” em {quando.astimezone().strftime('%d/%m/%Y %H:%M')} (1h).",
        conteudo=f"{titulo} — {quando.astimezone().strftime('%d/%m/%Y %H:%M')}",
        params={"titulo": titulo, "inicio_utc": quando.isoformat(), "duracao_min": 60},
    )
    return [Mensagem(
        f"Evento pronto para o **Calendário**:\n· {titulo}\n· {quando.astimezone().strftime('%d/%m/%Y %H:%M')} (1h)\n\n"
        f"Responda **aprovar {aprovacao_id}** para gravar (hash registrado)."
    )]


def _email_chat(texto: str) -> list[Mensagem] | None:
    """Enviar e-mail de verdade pelo Mail (com aprovação do conteúdo)."""
    m = re.search(
        r"\benvie\s+(?:um\s+)?e-?mail\s+para\s+([\w.+-]+@[\w.-]+)"
        r"(?:\s+com\s+(?:o\s+)?assunto\s+([^:,]+?))?"
        r"(?:\s+(?:dizendo|com\s+o\s+texto|escrevendo)|:)\s*([^'].*)$",
        texto, re.IGNORECASE,
    )
    if not m:
        m2 = re.search(r"\benvie\s+(?:um\s+)?e-?mail\s+para\s+([\w.+-]+@[\w.-]+)\s*$", texto, re.IGNORECASE)
        if not m2:
            return None
        return [Mensagem("O que devo escrever? Ex.: \"envie e-mail para foo@bar.com com assunto Proposta dizendo: Olá…\"")]
    para, assunto, corpo = m.group(1).strip(), (m.group(2) or "Contato").strip(), m.group(3).lstrip(":").strip()
    aprovacao_id = approvals.solicitar(
        tipo_acao="enviar_email_mail",
        resumo=f"ENVIAR e-mail real para {para} com assunto “{assunto}”. Ao aprovar, sai da sua caixa.",
        conteudo=corpo,
        params={"destinatario": para, "assunto": assunto},
    )
    return [Mensagem(
        f"E-mail pronto para **ENVIAR de verdade**:\n· Para: {para}\n· Assunto: {assunto}\n\n{corpo}\n\n"
        f"Responda **aprovar {aprovacao_id}** para enviar (hash registrado). Negar cancela."
    )]



def _buscar_info(texto: str) -> list[Mensagem] | None:
    """Consulta direta: endereço/telefone/site de estabelecimentos por nome + cidade.

    Fonte: OpenStreetMap/Nominatim (estabelecimentos reais); enriquece com a
    página oficial (telefone/e-mail extraídos dela) quando existe. Nada inventado:
    o que a fonte não tem, aparece como ausente.
    """
    n = _norm(texto)
    eh_lookup = bool(re.search(r"\b(informacoes|endereco|telefone|dados|onde fica|contatos?)\b", n))
    # "procure UM cardiologista que atenda no santa casa em SJC" = consulta pontual, não pesquisa em massa
    m_singular = re.search(r"\b(?:procure|ache|encontre|busque|quero|preciso de)\s+(um|uma)\s+", n)
    if not eh_lookup and not m_singular:
        return None

    alvo = None
    cidade = None
    qualificador = ""
    m_cidade = re.search(
        r"^.*\b(?:na cidade de|em|na|no)\s+([\wà-úÀ-Ú][\wà-úÀ-Ú\s]*?)\s*$",
        texto, re.IGNORECASE,
    )
    if m_cidade:
        cidade = m_cidade.group(1).strip()
    if m_singular:
        # "um cardiologista que atenda no santa casa em SJC" → alvo + qualificador + cidade
        corpo = n[m_singular.end():].strip()
        qualificador = ""
        if cidade and corpo.lower().endswith(cidade.lower()):
            corpo = corpo[: -len(cidade)].strip(" ,.")
        m_que = re.search(r"\bque\s+(.+)$", corpo)
        if m_que:
            qualificador = m_que.group(1).strip()
            alvo = corpo[:m_que.start()].strip(" .,")
        else:
            alvo = corpo.strip(" .,")
    else:
        m_aspas = re.search(r'["“]([^"”]{2,60})["”]', texto)
        if m_aspas:
            alvo = m_aspas.group(1).strip()
        else:
            m_alvo = re.search(
                r"\b(?:informacoes|endereco|telefone|dados|contatos?)\b\s*"
                r"(?:como\s+endereco\s+e\s+numero\s+de\s+telefone\s+)?"
                r"(?:(?:sobre|acerca\s+de|das?|dos?|de|da)\s+)(?:as\s+|os\s+)?"
                r"(?:lojas?|empresas?|clinicas?|escritorios?|academias?|oficinas?|restaurantes?|padarias?|concessionarias?)?\s*"
                r"(.+?)(?:\s+(?:na|no|em)\s+(?:cidade\s+de\s+)?[\w\s]+)?$",
                n, re.IGNORECASE,
            )
            if m_alvo and m_alvo.group(1).strip():
                alvo = m_alvo.group(1).strip()
    if not alvo or len(alvo) < 2:
        return None

    consulta = " ".join(x for x in (alvo, cidade) if x)
    from ..adapters import nominatim

    try:
        candidatos = nominatim.buscar(consulta, limite=6)
    except nominatim.FonteBloqueada as erro:
        return [Mensagem(f"A fonte pública (OpenStreetMap) bloqueou a consulta: {erro}. Tente de novo em instantes.")]
    except httpx.HTTPError as erro:
        return [Mensagem(f"Fonte pública indisponível agora ({type(erro).__name__}). Tente de novo em instantes.")]

    if not candidatos:
        # mapa vazio → resposta enriquecida completa: web + CNPJ (Receita) + conteúdo das páginas
        return busca_geral(f"{alvo} {cidade or ''}".strip())

    blocos = []
    for c in candidatos[:4]:
        telefone = c.get("telefone")
        site = c.get("site")
        enriquecido = ""
        if site:
            try:
                resultado = sites.coletar_site(site)
                if resultado.estado == "ok":
                    pagina = extrair_pagina(resultado.url_final, resultado.html)
                    extras = []
                    if pagina.telefones:
                        extras.append(f"telefone na página: {pagina.telefones[0]}")
                    if pagina.emails:
                        extras.append(f"e-mail: {pagina.emails[0]}")
                    if extras:
                        enriquecido = "\n   · " + " · ".join(extras)
            except Exception:
                enriquecido = ""
        bloco = (
            f"**{c['nome']}**\n"
            f"   · Endereço: {c['endereco']}\n"
            f"   · Telefone: {telefone or 'não registrado na fonte'}{enriquecido}\n"
            f"   · Site: {site or 'não registrado na fonte'}\n"
            f"   · Fonte: {c['fonte_url']} (coletado agora)"
        )
        blocos.append(bloco)

    nota_qualificador = ""
    if qualificador:
        nota_qualificador = (
            f"\n\nObs.: o filtro “{qualificador}” não é pesquisável na fonte pública — "
            "confirme essa condição no site ou telefone de cada resultado."
        )
    secao_web = ""
    try:
        web = websearch.buscar_detalhado(f"{alvo} {cidade or ''}".strip(), limite=4)
        if web:
            secao_web = "\n\n**Na web:**\n" + "\n".join(
                f"· [{r['titulo'][:80]}]({r['url']})" for r in web
            )
    except websearch.BuscaBloqueada:
        pass  # web indisponível agora; os dados do mapa já respondem

    return [Mensagem(
        f"Encontrei {len(blocos)} resultado(s) para “{alvo}”"
        + (f" em {cidade}" if cidade else "")
        + " (fonte: OpenStreetMap — telefones só aparecem se a fonte tiver):\n\n"
        + "\n\n".join(blocos) + nota_qualificador + secao_web
    )]


def _resumir_url(texto: str) -> list[Mensagem] | None:
    m = re.search(r"\b(?:resume|resuma|resumo)\s+(?:o\s+site\s+|a\s+p[áa]gina\s+|do\s+site\s+)?(https?://\S+)", texto, re.IGNORECASE)
    if not m:
        return None
    url = m.group(1)
    resultado = sites.coletar_site(url)
    if resultado.estado != "ok":
        return [Mensagem(f"Não consegui ler {url} ({resultado.estado}: {resultado.detalhe or 'bloqueio'}). "
                         "Alternativa: abra no seu navegador com a extensão conectada.")]
    from ..engine.evidence import extrair_entidades, extrair_pagina

    pagina = extrair_pagina(resultado.url_final, resultado.html)
    paragrafos = [p.strip() for p in pagina.texto.split("\n") if len(p.strip()) > 120]
    trecho = " ".join(paragrafos[:2])[:600] if paragrafos else pagina.texto[:400]
    return [Mensagem(
        f"**{pagina.titulo or url}** (resumo extrativo — sem LLM):\n"
        f"{pagina.meta_description or trecho}\n\nFonte: {resultado.url_final}",
        acoes=[{"rotulo": "Abrir página", "url": resultado.url_final}],
    )]


_SITES_CONHECIDOS = {
    "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com", "whatsapp": "https://web.whatsapp.com",
    "github": "https://github.com", "chatgpt": "https://chatgpt.com",
}


def _abrir_site(texto: str) -> list[Mensagem] | None:
    m = re.search(r"\babr[aei](?:r)?\s+(?:o\s+|a\s+|o\s+site\s+(?:do\s+)?|o\s+meu\s+)?([\w.]+(?:\.\w{2,})?|google|gmail|youtube|whatsapp|github|chatgpt)\b\s*$", texto, re.IGNORECASE)
    if not m:
        return None
    alvo = m.group(1).lower()
    url = _SITES_CONHECIDOS.get(alvo)
    if not url:
        url = alvo if alvo.startswith("http") else f"https://{alvo}"
        if "." not in url.replace("https://", "").replace("http://", ""):
            return None
    webbrowser.open(url)
    return [Mensagem(f"Abri {url} no seu navegador.")]


def _anotar(texto: str) -> list[Mensagem] | None:
    m = re.search(r"\banot[ea](?:\s+(?:na?\s+nota|no?\s+notes?|isso))?\s*[:,]?\s+(.{3,300})$", texto, re.IGNORECASE)
    if not m:
        return None
    conteudo = m.group(1).strip()
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_nota_notes",
        resumo="Criar anotação no Apple Notes (comando seu pelo chat).",
        conteudo=conteudo,
        params={"titulo": f"Anotação — {datetime.now().strftime('%d/%m/%Y %H:%M')}"},
    )
    return [Mensagem(
        f"Anotação pronta para gravar no Apple Notes:\n\n{conteudo}\n\n"
        f"Responda **aprovar {aprovacao_id}** para gravar (hash registrado)."
    )]




# ---------- busca geral: qualquer assunto, na web ----------

_PREENCHIMENTO = [
    r"^preciso\s+(?:que\s+voc[êe]\s+)?(?:me\s+)?",
    r"^voc[êe]\s+(?:pode|poderia|consegue)?\s*(?:me\s+)?",
    r"^por favor\s*",
    r"^me\s+d[êe]\s*",
    r"^d[êe]-me\s*",
    r"^me\s+passe\s*",
    r"^gostaria\s+de\s+saber\s*",
    r"^quero\s+(saber\s+)?",
    r"^preciso\s+de\s*",
    r"\bnomes?\s+(?:dos?|de|das|do)\s+",
    r"\bme\s+d[êe]\b",
    r"^procure\s+", r"^ache\s+", r"^encontre\s+", r"^busque\s+", r"^busca\s+",
    r"^quais\s+(?:s[ãa]o\s+)?", r"^quem\s+(?:s[ãa]o|é|e)\s*",
    r"^uma\s+lista\s+de\s+",
    r"^de\s+",
]

# cláusula negativa: "não quero sites para agendamentos, quero X" → mantém X
_PADRAO_NEGATIVA = re.compile(r"^\s*n[ãa]o\s+quero\b.*?\bquero\s+", re.IGNORECASE | re.DOTALL)


def _limpar_consulta(texto: str) -> str:
    """Remove preenchimento conversacional e cláusulas negativas ("não quero X, quero Y")."""
    consulta = texto.strip().rstrip("?!. ")
    consulta = _PADRAO_NEGATIVA.sub("", consulta)
    for padrao in _PREENCHIMENTO:
        consulta = re.sub(padrao, " ", consulta, flags=re.IGNORECASE).strip()
    # passada final: artigos/preposições que sobraram no início ("de carro de luxo" → "carro de luxo")
    anterior = None
    while anterior != consulta:
        anterior = consulta
        consulta = re.sub(r"^(de|da|do|dos|das|o|a|os|as|um|uma|para|pra)\s+", "", consulta, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", consulta).strip()


def extrair_nomes_pessoas(texto: str) -> list[str]:
    """Extrai nomes de pessoas (Dr./Dra. + nome próprio) de um texto de página.

    Heurística conservadora: apenas nomes com título Dr./Dra. — o suficiente
    para diretórios médicos e páginas de clínicas; deduplicado.
    """
    nomes: list[str] = []
    for m in re.finditer(
        r"\b(Dr|Dra)\.?\s+([A-ZÀ-Ú][\wà-úÀ-Ú'’.-]+(?:\s+(?:de|da|do|dos|das)\s+[A-ZÀ-Ú][\wà-úÀ-Ú'’.-]+"
        r"|\s+e\s+(?!Dra?\b)[A-ZÀ-Ú][\wà-úÀ-Ú'’.-]+|\s+[A-ZÀ-Ú][\wà-úÀ-Ú'’.-]+){0,3})",
        texto,
    ):
        nome = f"{m.group(1)}. {m.group(2)}"
        if nome not in nomes:
            nomes.append(nome)
    return nomes


def lista_nomes(texto: str) -> list[Mensagem] | None:
    """Lista de NOMES de pessoas/profissionais: busca, abre as páginas e extrai.

    Ex.: "nomes dos cardiologistas que trabalham no Santa Casa em SJC".
    Cada nome traz a fonte de onde foi extraído; se as páginas não expõem
    nomes em texto (agendamento via JavaScript), diz com honestidade.
    """
    n = _norm(texto)
    gatilho_pessoa = bool(re.search(
        r"\b(nomes?|quem (s[ãa]o|trabalha|trabalham|atende|atendem)|lista de)\b", n
    )) and bool(re.search(
        r"\b(dras?\.?|medic[oa]s?|cardiologistas?|dentistas?|advogad[oa]s?|professores?|professoras?"
        r"|profissionais?|especialistas?|pessoas?|engenheir[oa]s?|nutricionistas?|psic[óo]log[oa]s?"
        r"|veterin[áa]ri[oa]s?|fisioterapeutas?|dermatologistas?|pediatras?)\b", n
    ))
    if not gatilho_pessoa:
        return None
    consulta = _limpar_consulta(texto)
    if len(consulta) < 2:
        return None

    try:
        resultados = websearch.buscar_detalhado(consulta, limite=5)
    except websearch.BuscaBloqueada as erro:
        return [Mensagem(
            f"Os motores de busca estão bloqueando este IP agora ({erro}). "
            "É temporário — tente de novo em alguns minutos. Registrei a limitação."
        )]
    if not resultados:
        return [Mensagem(f"Sem resultados na web para “{consulta}”.")]

    achados: list[str] = []
    fontes_sem_nomes: list[str] = []
    for r in resultados[:4]:
        try:
            coleta = sites.coletar_site(r["url"])
        except Exception:  # noqa: BLE001
            fontes_sem_nomes.append(r["url"])
            continue
        if coleta.estado != "ok":
            fontes_sem_nomes.append(r["url"])
            continue
        pagina = extrair_pagina(coleta.url_final, coleta.html)
        nomes = extrair_nomes_pessoas(r.get("titulo", "") + "\n" + pagina.texto)
        if nomes:
            achados.append(f"**Via {urlparse(coleta.url_final).netloc}** ({coleta.url_final}):\n"
                           + "\n".join(f"· {n}" for n in nomes[:8]))
        else:
            fontes_sem_nomes.append(coleta.url_final)

    if achados:
        extra = (f"\n\nNomes não extraídos de {len(fontes_sem_nomes)} fonte(s) "
                 f"(carregam via agendamento em JavaScript): {', '.join(fontes_sem_nomes)}") if fontes_sem_nomes else ""
        return [Mensagem(
            f"Nomes encontrados para “{consulta}”:\n\n" + "\n\n".join(achados) + extra
        )]
    return [Mensagem(
        f"As páginas sobre “{consulta}” não expõem nomes em texto legível "
        f"(a maioria usa agendamento em JavaScript). Abra-as diretamente:\n"
        + "\n".join(f"· {u}" for u in fontes_sem_nomes[:5])
    )]


def busca_geral(texto: str) -> list[Mensagem]:
    """Busca na web para QUALQUER assunto (pessoas, profissionais, temas, lugares).

    Limpa o preenchimento conversacional, pesquisa e devolve título + link.
    Fonte: motores públicos (Brave → DuckDuckGo). Se bloquearem, diz com
    honestidade — nunca inventa.
    """
    consulta = _limpar_consulta(texto)
    if len(consulta) < 2:
        consulta = texto.strip()

    resultados = None
    ultimo_erro = None
    for tentativa in range(2):  # a janela do limite oscila rápido: 1 retry após pausa
        try:
            resultados = websearch.buscar_detalhado(consulta, limite=6)
            break
        except websearch.BuscaBloqueada as erro:
            ultimo_erro = erro
            if tentativa == 0:
                websearch.reset_cooldown()
                time.sleep(7)
    if resultados is None:
        return [Mensagem(
            f"Os motores de busca estão bloqueando este IP agora ({ultimo_erro}). "
            "É temporário — tente de novo em alguns minutos. Registrei a limitação."
        )]
    if not resultados:
        return [Mensagem(f"Sem resultados na web para “{consulta}”.")]

    print(">>> ROTA: busca_geral → web <<<", flush=True)

    # ---- fichas oficiais: CNPJs detectados em URLs/títulos → Receita (BrasilAPI) ----
    secao_cnpj = ""
    texto_resultados = " ".join(r["url"] + " " + r["titulo"] for r in resultados)
    cnpjs = list(dict.fromkeys(
        re.findall(r"\b(\d{2}\d{3}\d{3}\d{4}\d{2})\b", texto_resultados)
    ))[:2]
    for cnpj in cnpjs:
        try:
            dados = brasilapi.consultar_cnpj(cnpj)
        except Exception:  # noqa: BLE001
            dados = None
        if not dados:
            continue
        formatado = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        secao_cnpj += (
            f"\n\n🏢 **{dados['razao_social']}** — CNPJ {formatado}\n"
            f"   · Situação: {dados['situacao'] or '?'} · {dados['municipio']}/{dados['uf']}\n"
            f"   · CNAE: {dados['cnae_descricao'] or '?'}\n"
            f"   · Fonte: dados abertos da Receita Federal via BrasilAPI (coletado agora)"
        )
    if secao_cnpj:
        secao_cnpj = "**Registros oficiais encontrados (Receita Federal):**" + secao_cnpj

    # fontes múltiplas: lugares no mapa (OSM) + conteúdo das 3 principais páginas
    secao_lugares = ""
    try:
        lugares = nominatim.buscar(consulta, limite=4)
        if lugares:
            secao_lugares = "\n\n**No mapa (OpenStreetMap):**\n" + "\n".join(
                f"· **{l['nome']}** — {l['endereco'][:90]} ([fonte]({l['fonte_url']}))"
                for l in lugares
            )
    except Exception:  # noqa: BLE001 — mapa é fonte extra; falha não derruba a resposta
        pass

    secao_detalhes = ""
    for r in resultados[:3]:
        try:
            coleta = sites.coletar_site(r["url"])
        except Exception:  # noqa: BLE001
            continue
        if coleta.estado != "ok":
            continue
        pagina = extrair_pagina(coleta.url_final, coleta.html)
        entidades = extrair_entidades(coleta.html)[:5]
        partes = [f"**{r['titulo'][:80]}** ({urlparse(coleta.url_final).netloc})"]
        if pagina.meta_description:
            partes.append(pagina.meta_description[:220])
        contato = []
        if pagina.telefones:
            contato.append(f"tel: {pagina.telefones[0]}")
        if pagina.emails:
            contato.append(f"e-mail: {pagina.emails[0]}")
        if contato:
            partes.append(" · ".join(contato))
        if entidades:
            partes.append("citam: " + ", ".join(entidades))
        secao_detalhes += "\n\n· " + " · ".join(partes)

    from urllib.parse import quote

    acoes = [
        {"rotulo": "🗺 Ver no Google Maps", "url": "https://www.google.com/maps/search/?api=1&query=" + quote(consulta)},
        {"rotulo": "🔎 Abrir no Google", "url": "https://www.google.com/search?q=" + quote(consulta)},
        {"rotulo": "🌍 Ver no OpenStreetMap", "url": "https://www.openstreetmap.org/search?query=" + quote(consulta)},
    ]

    linhas = [f"**[{i}. {r['titulo'][:90]}]({r['url']})**" for i, r in enumerate(resultados, 1)]
    corpo = f"Busca por “{consulta}” — resultados na **web** (fontes públicas, agora):\n\n" + "\n\n".join(linhas)
    if secao_cnpj:
        corpo += "\n\n" + secao_cnpj
    if secao_detalhes:
        corpo += "\n\n**O que as principais páginas trazem:**" + secao_detalhes
    if secao_lugares:
        corpo += secao_lugares
    corpo += "\n\nQuer que eu investigue alguma dessas fontes mais a fundo? Me diga o número ou o site."
    return [Mensagem(corpo, acoes=acoes)]



# ---------- integração no chat: perguntas abertas ----------

def resposta_livre(texto: str) -> list[Mensagem]:
    return _livre(texto)
