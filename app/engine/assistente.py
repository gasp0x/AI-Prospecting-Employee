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
import threading
import time
import unicodedata
import webbrowser
from datetime import datetime, timedelta, timezone

import httpx

from .. import db
from ..adapters import sites
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

def tentar(texto: str) -> list[Mensagem] | None:
    """Tenta as habilidades determinísticas; None = não é comando conhecido."""
    for handler in (_hora_data, _calculo, _cotacao, _clima, _lembrete_criar,
                    _lembrete_listar, _buscar_info, _resumir_url, _abrir_site, _anotar):
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


def _buscar_info(texto: str) -> list[Mensagem] | None:
    """Consulta direta: endereço/telefone/site de estabelecimentos por nome + cidade.

    Fonte: OpenStreetMap/Nominatim (estabelecimentos reais); enriquece com a
    página oficial (telefone/e-mail extraídos dela) quando existe. Nada inventado:
    o que a fonte não tem, aparece como ausente.
    """
    n = _norm(texto)
    if not re.search(r"\b(informacoes|endereco|telefone|dados|onde fica|contatos?)\b", n):
        return None

    alvo = None
    cidade = None
    m_cidade = re.search(r"(?:na cidade de|em|na|no)\s+([\wà-úÀ-Ú\s]+?)\s*$", texto, re.IGNORECASE)
    if m_cidade:
        cidade = m_cidade.group(1).strip()
    m_aspas = re.search(r'["“]([^"”]{2,60})["”]', texto)
    if m_aspas:
        alvo = m_aspas.group(1).strip()
    else:
        m_alvo = re.search(
            r"\b(?:informacoes|endereco|telefone|dados|contatos?)\b\s*"
            r"(?:como\s+endereco\s+e\s+numero\s+de\s+telefone\s+)?"
            r"(?:das?|dos?|de|da)\s+(?:as\s+|os\s+)?"
            r"(?:lojas?|empresas?|clinicas?|escritorios?|academias?|oficinas?|restaurantes?|padarias?|concessionarias?)?\s*"
            r"(.+?)(?:\s+(?:na|no|em)\s+(?:cidade\s+de\s+)?[\w\s]+)?$",
            n, re.IGNORECASE,
        )
        if m_alvo and m_alvo.group(1).strip():
            alvo = m_alvo.group(1).strip()
    if not alvo or len(alvo) < 2:
        return None

    consulta = f"{alvo} {cidade}".strip() if cidade else alvo
    from ..adapters import nominatim

    try:
        candidatos = nominatim.buscar(consulta, limite=6)
    except nominatim.FonteBloqueada as erro:
        return [Mensagem(f"A fonte pública (OpenStreetMap) bloqueou a consulta: {erro}. Tente de novo em instantes.")]
    except httpx.HTTPError as erro:
        return [Mensagem(f"Fonte pública indisponível agora ({type(erro).__name__}). Tente de novo em instantes.")]

    if not candidatos:
        return [Mensagem(
            f"Não encontrei estabelecimentos com “{alvo}”"
            + (f" em {cidade}" if cidade else "")
            + " no OpenStreetMap. Isso significa ausência na base pública — não que não exista. "
              "Posso tentar uma pesquisa completa se você disser: procure " + alvo + " em " + (cidade or "sua cidade") + "."
        )]

    blocos = []
    for c in candidatos[:4]:
        telefone = c.get("telefone")
        site = c.get("site")
        enriquecido = ""
        if site:
            try:
                resultado = sites.coletar_site(site)
                if resultado.estado == "ok":
                    from ..engine.evidence import extrair_pagina

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

    return [Mensagem(
        f"Encontrei {len(blocos)} resultado(s) para “{alvo}”"
        + (f" em {cidade}" if cidade else "")
        + " (fonte: OpenStreetMap — telefones só aparecem se a fonte tiver):\n\n"
        + "\n\n".join(blocos)
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
    from ..engine.evidence import extrair_pagina

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


# ---------- integração no chat: perguntas abertas ----------

def resposta_livre(texto: str) -> list[Mensagem]:
    return _livre(texto)
