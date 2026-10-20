"""Cérebro conversacional do funcionário: mensagem em lingu natural → ação → resposta.

Canais (web, Telegram, futuros) chamam `responder()` e recebem uma lista de
mensagens de resposta. As intenções são determinísticas (pt-BR, testáveis);
nenhum LLM é necessário — o mesmo cérebro funciona offline.

Intenções: ajuda · status · resultados · detalhes/rascunho de empresa ·
aprovar/negar · aprovações pendentes · exportar · navegador · e o principal:
descrever um ICP ("procure clínicas de fisioterapia em Campinas") inicia a
pesquisa completa e o funcionário reporta o resultado no próprio chat.
"""
from __future__ import annotations

import re
import unicodedata

from .. import db
from ..adapters.browser import ponte_navegador
from ..config import settings
from ..engine import approvals, drafting, research
from ..engine.icp import extrair_criterios
from ..engine.tasks import ROTULOS_ESTADO, gerenciador_tarefas


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower().strip()


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


def responder(texto: str) -> list[Mensagem]:
    """Interpreta a mensagem e executa a ação. Retorna as mensagens de resposta."""
    n = _norm(texto)

    if re.search(r"\b(ajuda|help|comandos|o que (voce|vc) faz|como funciona)\b", n):
        return [_ajuda()]

    if re.search(r"\b(status|andamento|progresso|como (esta|vai) (a |as )?(pesquisa|tarefas))\b", n):
        return [_status()]

    if re.search(r"\b(aprovacoes|pendencias|o que (esta|ta) pendente)\b", n):
        return _aprovacoes_pendentes()

    m_aprovar = re.match(r"^(aprovar?|ok)\s*(#?\d+)?$", n)
    if m_aprovar:
        return _decidir_aprovacao(m_aprovar.group(2), True)
    m_negar = re.match(r"^(negar|rejeitar)\s*(#?\d+)?$", n)
    if m_negar:
        return _decidir_aprovacao(m_negar.group(2), False)

    if re.search(r"\bexportar?\b", n):
        return [_exportar()]

    if re.search(r"\b(navegador|extensao|whatsapp do chrome|aba autenticada)\b", n):
        return [_navegador()]

    m_rascunho = re.search(r"\b(?:rascunho|mensagem|escrever?|abordagem|email?|e-mail)\s+(?:para|pra|da|do)\s+(.{3,80})$", n)
    if m_rascunho:
        return _rascunho(m_rascunho.group(1))

    if re.search(r"\b(resultados?|empresas? (encontradas|achadas|qualificadas)|o que (voce|vc) (achou|encontrou)|lista|melhores)\b", n):
        return [_resultados()]

    # descrição de ICP: "procure/ache/encontre X" ou texto longo com segmento
    if re.search(r"\b(procur[ea]?|ach[ea]?|encontr[ea]?|busqu?e|busca[r]?|prospector?|liste)\b", n) or len(n.split()) >= 5:
        return _novo_icp(texto)

    return [
        Mensagem(
            "Não entendi. Você pode, por exemplo:\n"
            '· "procure clínicas de fisioterapia em Campinas (SP)"\n'
            '· "status"\n· "resultados"\n· "rascunho para Nexxus Fisioterapia"\n'
            '· "aprovações" · "aprovar 3" · "exportar"\n· "ajuda"'
        )
    ]


# ---------- intenções ----------

def _ajuda() -> Mensagem:
    return Mensagem(
        "Sou seu funcionário de prospecção. Fale comigo em português:\n\n"
        "🔍 **Novo ICP** — \"procure clínicas de fisioterapia em Campinas (SP), com site próprio\"\n"
        "   → eu extraio os critérios, pesquiso em fontes públicas e te reporto as qualificadas\n"
        "📊 **Status** — \"status\" (andamento da pesquisa em curso)\n"
        "🏢 **Resultados** — \"resultados\" ou \"resultados do ICP 2\"\n"
        "✍️ **Rascunho** — \"rascunho para Nexxus Fisioterapia\" (gera a mensagem com as evidências)\n"
        "✅ **Aprovações** — \"aprovações\" lista; \"aprovar 3\" ou \"negar 3\" decide\n"
        "📦 **Exportar** — \"exportar\" (links CSV/JSON)\n"
        "🌐 **Navegador** — \"navegador\" (estado da extensão)\n\n"
        "Regras que eu nunca quebro: só afirmo o que coletei de fontes reais (com URL e data) "
        "e nenhuma ação externa acontece sem sua aprovação com o conteúdo exato."
    )


def _status() -> Mensagem:
    tarefas = db.listar("tarefa", ordem="id DESC", limite=3)
    if not tarefas:
        return Mensagem("Nenhuma tarefa ainda. Me dê um ICP: \"procure dentistas em Curitiba\".")
    linhas = []
    for t in tarefas:
        rotulo = {"pesquisa": "Pesquisa"}.get(t["tipo"], t["tipo"])
        linhas.append(f"#{t['id']} · {rotulo} · {ROTULOS_ESTADO.get(t['estado'], t['estado'])} · {t['progresso']}%")
    return Mensagem("Tarefas recentes:\n" + "\n".join(linhas))


def _resultados() -> Mensagem:
    empresas = db.listar(
        "empresa", where="estado IN ('nova','qualificada')", ordem="pontuacao DESC", limite=8
    )
    if not empresas:
        return Mensagem("Ainda não tenho empresas qualificadas. Me dê um ICP para começar a pesquisa.")
    linhas = [f"**{e['nome']}** — {e['pontuacao']:.0f} pts · {e['dominio'] or e['localizacao'] or 'sem site'}" for e in empresas]
    return Mensagem(
        "Melhores empresas (todos os ICPs):\n" + "\n".join(linhas) +
        "\n\nPeça \"rascunho para <nome>\" para preparar a abordagem.",
        acoes=[{"rotulo": "Ver detalhes e fontes", "url": "/resultados"}],
    )


def _novo_icp(texto: str) -> list[Mensagem]:
    extraido = extrair_criterios(texto)
    criterios = extraido["criterios"]
    if not criterios:
        return [Mensagem(
            "Não consegui identificar um segmento de negócio nessa descrição. "
            "Tente algo como: \"procure academias em São Paulo (SP) que usem check-in digital\"."
        )]
    icp_id = db.inserir(
        "icp",
        {
            "texto_bruto": texto.strip(),
            "criterios_json": db.para_json(criterios),
            "proposta_valor": texto.strip(),
            "assinatura": settings.assinatura,
            "origem": "automatico",
            "criado_em": db.agora(),
            "atualizado_em": db.agora(),
        },
    )
    tarefa_id = gerenciador_tarefas.criar(tipo="pesquisa", params={"icp_id": icp_id}, icp_id=icp_id)
    gerenciador_tarefas.iniciar(tarefa_id, research.executar_pesquisa)
    db.registrar_auditoria("usuario", "pesquisa_iniciada_chat", {"icp_id": icp_id, "tarefa_id": tarefa_id})
    termos = ", ".join(c["termo"] for c in criterios)
    return [
        Mensagem(
            f"Interpretei seu ICP com {len(criterios)} critério(s): {termos}.\n"
            f"Pesquisa **#{tarefa_id}** iniciada em fontes públicas (OpenStreetMap, sites oficiais, "
            "busca web quando disponível). Te reporto as qualificadas quando terminar — "
            "pode acompanhar com \"status\".",
            tarefa_id=tarefa_id,
        )
    ]


def _rascunho(nome_busca: str) -> list[Mensagem]:
    termo = _norm(nome_busca)
    candidatos = [
        e for e in db.listar("empresa", where="estado != 'duplicata'", ordem="pontuacao DESC", limite=100)
        if termo in _norm(e["nome"])
    ]
    if not candidatos:
        return [Mensagem(f"Não encontrei nenhuma empresa com “{nome_busca}” nos resultados. Veja \"resultados\".")]
    empresa = candidatos[0]
    icp = db.buscar_um("icp", empresa["icp_id"])
    evidencias = db.de_json(empresa["evidencias_json"], [])
    texto, _usou_llm = drafting.redigir_assistido(
        empresa["nome"], empresa["localizacao"] or "", evidencias,
        icp["proposta_valor"] if icp else "",
        (icp["assinatura"] if icp else "") or "",
    )
    existente = db.listar("rascunho", where="empresa_id = ?", parametros=(empresa["id"],), ordem="id DESC", limite=1)
    if existente:
        db.atualizar("rascunho", int(existente[0]["id"]), {"conteudo": texto, "atualizado_em": db.agora()})
        rascunho_id = int(existente[0]["id"])
    else:
        rascunho_id = db.inserir("rascunho", {"empresa_id": empresa["id"], "conteudo": texto, "criado_em": db.agora(), "atualizado_em": db.agora()})
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_rascunho_mail",
        resumo=f"Criar RASCUNHO no Apple Mail para “{empresa['nome']}” (não envia).",
        conteudo=texto,
        params={"destinatario": "", "assunto": f"{empresa['nome']} — abordagem comercial", "rascunho_id": rascunho_id, "empresa_id": empresa["id"]},
    )
    return [
        Mensagem(
            f"Rascunho para **{empresa['nome']}** ({empresa['pontuacao']:.0f} pts, baseado em "
            f"{len(evidencias)} evidência(s)):\n\n{texto}\n\n"
            f"Para criar o rascunho no Mail, responda **aprovar {aprovacao_id}** — o conteúdo acima "
            "é o exato que será executado (hash SHA-256 registrado).",
            acoes=[{"rotulo": f"Ver empresa #{empresa['id']}", "url": f"/empresa/{empresa['id']}"}],
        )
    ]


def _aprovacoes_pendentes() -> list[Mensagem]:
    pendentes = db.listar("aprovacao", where="estado = 'pendente'", ordem="id DESC")
    if not pendentes:
        return [Mensagem("Nenhuma aprovação pendente. ✓")]
    msgs = []
    for a in pendentes:
        msgs.append(Mensagem(
            f"**Aprovação #{a['id']}** · {a['tipo_acao']}\n{a['resumo']}\n\n---\n{a['conteudo']}\n---\n\n"
            f"SHA-256: `{a['conteudo_sha256'][:24]}…`\nResponda **aprovar {a['id']}** ou **negar {a['id']}**.",
            acoes=[{"rotulo": "Abrir aprovações", "url": "/aprovacoes"}],
        ))
    return msgs


def _decidir_aprovacao(num_txt: str | None, aprovar: bool) -> list[Mensagem]:
    pendentes = db.listar("aprovacao", where="estado = 'pendente'", ordem="id DESC")
    if num_txt:
        aprovacao_id = int(num_txt.lstrip("#"))
    elif len(pendentes) == 1:
        aprovacao_id = int(pendentes[0]["id"])
    else:
        return [Mensagem("Diga qual aprovação: \"aprovar 3\". Veja as pendentes com \"aprovações\".")]
    registro = db.buscar_um("aprovacao", aprovacao_id)
    if registro is None:
        return [Mensagem(f"Aprovação #{aprovacao_id} não existe.")]
    if registro["estado"] != "pendente":
        return [Mensagem(f"Aprovação #{aprovacao_id} já estava {registro['estado']}.")]
    try:
        resultado = approvals.decidir(aprovacao_id, aprovar)
    except ValueError as erro:
        return [Mensagem(f"Não deu: {erro}")]
    if not aprovar:
        return [Mensagem(f"Aprovação #{aprovacao_id} **negada**. Nada foi executado. ✓")]
    return [
        Mensagem(
            f"Aprovação #{aprovacao_id} aprovada — executando com verificação de hash. "
            "Acompanhe: \"status\".",
            tarefa_id=resultado.get("tarefa_id"),
        )
    ]


def _exportar() -> Mensagem:
    icps = db.listar("icp", ordem="id DESC", limite=1)
    qs = f"?icp={icps[0]['id']}" if icps else ""
    return Mensagem(
        "Exportação pronta (CSV abre no Excel pt-BR; JSON traz fontes e evidências):",
        acoes=[
            {"rotulo": "⤓ CSV", "url": f"/exportar/empresas{qs}&formato=csv"},
            {"rotulo": "⤓ JSON", "url": f"/exportar/empresas{qs}&formato=json"},
            {"rotulo": "Todas as opções", "url": "/exportar"},
        ],
    )


def _navegador() -> Mensagem:
    s = ponte_navegador.status()
    if s["conectado"]:
        return [Mensagem(f"Navegador conectado ✓ ({s['navegador']}). Posso ler abas autenticadas quando você autorizar cada leitura.")]
    return [Mensagem(
        "Navegador desconectado. Abra o ícone da extensão no Chrome e clique em **Conectar** — "
        "instruções completas em /extensao.",
        acoes=[{"rotulo": "Instruções", "url": "/extensao"}],
    )]


# ---------- notificações de tarefas (pesquisa concluída → mensagem) ----------

def puxar_notificacoes() -> list[Mensagem]:
    """Sintetiza mensagens para tarefas de pesquisa concluídas ainda não reportadas."""
    nao_notificadas = db.listar(
        "tarefa",
        where="tipo = 'pesquisa' AND estado IN ('concluida','falha','cancelada','expirada') "
              "AND id NOT IN (SELECT COALESCE(tarefa_id, 0) FROM chat_msg WHERE tarefa_id IS NOT NULL)",
        ordem="id ASC",
        limite=10,
    )
    msgs: list[Mensagem] = []
    for t in nao_notificadas:
        if t["estado"] == "concluida":
            r = db.de_json(t["resultado_json"], {}) or {}
            icp_id = r.get("icp_id")
            top = ""
            if icp_id:
                melhores = db.listar(
                    "empresa", where="icp_id = ? AND estado = 'qualificada'",
                    parametros=(icp_id,), ordem="pontuacao DESC", limite=5,
                )
                if melhores:
                    top = "\n\nMelhores:\n" + "\n".join(
                        f"· **{e['nome']}** — {e['pontuacao']:.0f} pts · {e['dominio'] or e['localizacao'] or 'sem site'}"
                        for e in melhores
                    )
            msgs.append(Mensagem(
                f"✅ Pesquisa **#{t['id']}** concluída: {r.get('empresas_criadas', '?')} empresas "
                f"({r.get('qualificadas', '?')} qualificadas, {r.get('bloqueios_site', 0)} bloqueios).{top}\n"
                "Peça \"rascunho para <empresa>\" ou \"exportar\".",
                tarefa_id=t["id"],
                acoes=[{"rotulo": "Ver todas", "url": f"/resultados?icp={icp_id or ''}"}],
            ))
        elif t["estado"] == "cancelada":
            msgs.append(Mensagem(f"✕ Pesquisa **#{t['id']}** cancelada (progresso mantido).", tarefa_id=t["id"]))
        elif t["estado"] == "expirada":
            msgs.append(Mensagem(f"⏱ Pesquisa **#{t['id']}** expirou por tempo limite.", tarefa_id=t["id"]))
        else:
            msgs.append(Mensagem(f"⚠ Pesquisa **#{t['id']}** falhou: {t['erro']}", tarefa_id=t["id"]))
        registrar_agente(msgs[-1], tarefa_id=t["id"])
    return msgs
