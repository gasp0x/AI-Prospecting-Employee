"""Redação do rascunho de abordagem comercial.

Regras anti-invenção:
- O "gancho" da mensagem usa APENAS evidências reais coletadas (trecho + fonte).
- Sem LLM: modelo de mensagem em pt-BR com os fatos disponíveis.
- Com LLM: o modelo reescreve recebendo os fatos e instruções explícitas para
  não inventar informações — o resultado é auxiliado, não fonte de verdade.
"""
from __future__ import annotations

from . import llm

_SYSTEM_REDACAO = (
    "Você é um redator comercial. Reescreva a mensagem de prospecção mantendo TODAS as "
    "informações factuais exatamente como fornecidas. É PROIBIDO inventar nomes, números, "
    "serviços, clientes ou qualquer fato não presente nos dados. Responda apenas com o texto "
    "da mensagem, em português do Brasil, tom profissional e cordial, no máximo 150 palavras."
)


def _gancho_evidencia(evidencias: list[dict]) -> str:
    """Escolhe a evidência mais forte (título > texto) para abrir a mensagem."""
    for campo in ("titulo", "texto"):
        for e in evidencias:
            if e.get("campo") == campo and e.get("trecho"):
                trecho = e["trecho"].strip()
                if campo == "titulo":
                    trecho = trecho.replace("[título da página]", "").strip()
                return trecho
    return ""


def redigir(
    nome_empresa: str,
    localizacao: str,
    evidencias: list[dict],
    proposta_valor: str,
    assinatura: str,
) -> str:
    """Gera o rascunho determinístico (sempre disponível)."""
    gancho = _gancho_evidencia(evidencias)
    proposta = (proposta_valor or "").strip() or "ajudar empresas como a sua"
    linhas = [f"Olá, equipe {nome_empresa}!", ""]
    if gancho:
        linhas += [
            f"Encontrei o site de vocês e um trecho que me chamou a atenção: “{gancho}”",
            "",
        ]
    else:
        linhas += ["Encontrei o perfil de vocês durante uma pesquisa de mercado.", ""]
    if localizacao:
        linhas.append(f"Pelo que verifiquei, vocês atuam em {localizacao}. ")
    linhas += [
        f"Trabalho com {proposta} e acredito que possa fazer sentido para a {nome_empresa}.",
        "",
        "Se houver interesse, posso enviar mais detalhes em uma conversa rápida.",
        "",
        f"{assinatura}".strip() or "Atenciosamente,",
    ]
    return "\n".join(linhas).strip()


def redigir_assistido(
    nome_empresa: str,
    localizacao: str,
    evidencias: list[dict],
    proposta_valor: str,
    assinatura: str,
) -> tuple[str, bool]:
    """Tenta redigir com LLM usando apenas os fatos; devolve (texto, usou_llm)."""
    base = redigir(nome_empresa, localizacao, evidencias, proposta_valor, assinatura)
    fatos = [
        {"empresa": nome_empresa, "localizacao": localizacao, "evidencias": evidencias[:5]}
    ]
    pedido = (
        f"Fatos verificados (única fonte permitida): {fatos}\n"
        f"Proposta de valor do remetente: {proposta_valor!r}\n"
        f"Assinatura: {assinatura!r}\n"
        f"Mensagem base para melhorar: {base!r}"
    )
    resposta = llm.completar_json(_SYSTEM_REDACAO, pedido)
    if isinstance(resposta, dict) and isinstance(resposta.get("mensagem"), str) and resposta["mensagem"].strip():
        return resposta["mensagem"].strip(), True
    return base, False
