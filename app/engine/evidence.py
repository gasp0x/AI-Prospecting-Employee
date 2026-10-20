"""Evidências: extração de texto, trechos verificáveis e metadados de páginas.

Princípios:
- Todo trecho de evidência guarda: texto exato (janela limitada), URL de origem,
  data/hora da coleta e o critério a que se refere.
- Correspondência de termos ignora acentos e caixa, mas o trecho registrado é
  o texto ORIGINAL da página (nada é parafraseado).
- Distinção fato × inferência é feita na qualificação (scoring), não aqui.
"""
from __future__ import annotations

import hashlib
import html as html_mod
import re
import unicodedata
from dataclasses import dataclass, field

LIMITE_BYTES_HTML = 2_000_000

_RE_SCRIPT = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_RE_COMENTARIO = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_ESPACO = re.compile(r"\s+")


@dataclass
class PaginaColetada:
    url: str
    titulo: str = ""
    texto: str = ""
    meta_description: str = ""
    h1: str = ""
    cnpjs: list[str] = field(default_factory=list)
    telefones: list[str] = field(default_factory=list)
    ceps: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def html_para_texto(conteudo_html: str) -> str:
    sem_scripts = _RE_SCRIPT.sub(" ", conteudo_html)
    sem_comentarios = _RE_COMENTARIO.sub(" ", sem_scripts)
    quebras = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", sem_comentarios, flags=re.IGNORECASE)
    sem_tags = _RE_TAG.sub(" ", quebras)
    texto = html_mod.unescape(sem_tags)
    texto = _RE_ESPACO.sub(" ", texto)
    return "\n".join(linha.strip() for linha in texto.split("\n") if linha.strip())


def _primeiro(padrao: re.Pattern, html: str) -> str:
    m = re.search(padrao, html, re.IGNORECASE | re.DOTALL)
    return html_mod.unescape(m.group(1)).strip() if m else ""


def extrair_pagina(url: str, conteudo_html: str) -> PaginaColetada:
    """Extrai título, h1, description, texto e identificadores (CNPJ, telefone, CEP, e-mail)."""
    texto = html_para_texto(conteudo_html)
    pagina = PaginaColetada(url=url, texto=texto[:200_000])
    pagina.titulo = _primeiro(r"<title[^>]*>(.*?)</title>", conteudo_html)[:300]
    pagina.h1 = _primeiro(r"<h1[^>]*>(.*?)</h1>", conteudo_html)[:300]
    pagina.meta_description = _primeiro(
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']", conteudo_html
    )[:500]
    if not pagina.meta_description:
        pagina.meta_description = _primeiro(
            r"<meta[^>]+content=[\"'](.*?)[\"'][^>]+name=[\"']description[\"']", conteudo_html
        )[:500]

    # CNPJ no formato 00.000.000/0000-00 (apenas dígitos formatados; sem inventar)
    vistos: set[str] = set()
    for m in re.finditer(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", conteudo_html):
        if m.group(0) not in vistos:
            vistos.add(m.group(0))
            pagina.cnpjs.append(m.group(0))

    for padrao, alvo in (
        (r"(?:\+?55)?\s?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}", pagina.telefones),
        (r"\d{5}-\d{3}", pagina.ceps),
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", pagina.emails),
    ):
        vistos = set()
        for m in re.finditer(padrao, texto):
            if m.group(0) not in vistos:
                vistos.add(m.group(0))
                alvo.append(m.group(0))
            if len(alvo) >= 3:
                break
    return pagina


def _janela_trecho(texto: str, inicio: int, fim: int, margem: int = 120) -> str:
    a = max(0, inicio - margem)
    b = min(len(texto), fim + margem)
    trecho = texto[a:b].replace("\n", " ").strip()
    prefixo = "…" if a > 0 else ""
    sufixo = "…" if b < len(texto) else ""
    return f"{prefixo}{trecho}{sufixo}"


def coletar_evidencias(
    pagina: PaginaColetada,
    criterios: list[dict],
    url_origem: str,
    data_coleta: str,
    max_por_termo: int = 3,
) -> list[dict]:
    """Encontra trechos da página que correspondem a cada critério.

    Retorna lista de evidências:
    {criterio_id, termo, trecho, url, data_coleta, fonte_titulo, campo}
    `campo` indica onde o termo apareceu (texto|titulo|h1|description).
    """
    texto_busca = _sem_acento(pagina.texto.lower())
    evidencias: list[dict] = []

    def procurar(termo: str, conteudo: str, conteudo_busca: str, campo: str) -> int:
        termo_busca = _sem_acento(termo.lower())
        if not termo_busca:
            return 0
        achados = 0
        pos = 0
        ultima_pos_aceita = -10_000
        while achados < max_por_termo:
            idx = conteudo_busca.find(termo_busca, pos)
            if idx < 0:
                break
            pos = idx + len(termo_busca)
            # descarta janelas sobrepostas à última aceita (evidências repetidas)
            if idx - ultima_pos_aceita < 150:
                continue
            ultima_pos_aceita = idx
            fim = idx + len(termo_busca)
            evidencias.append(
                {
                    "criterio_id": None,  # preenchido pelo chamador
                    "termo": termo,
                    "trecho": _janela_trecho(conteudo, idx, fim),
                    "url": url_origem,
                    "data_coleta": data_coleta,
                    "fonte_titulo": pagina.titulo,
                    "campo": campo,
                }
            )
            achados += 1
        return achados

    for criterio in criterios:
        termo = criterio["termo"]
        antes = len(evidencias)
        # critérios compostos (ex.: "mais de 50 funcionários") → busca pela parte numérica
        partes = [termo]
        if criterio["tipo"] == "porte":
            m = re.search(r"mais de (\d+)", termo)
            if m:
                partes = [m.group(1)]
        for termo_busca_atual in partes:
            procurar(termo_busca_atual, pagina.texto, texto_busca, "texto")
            if criterio["tipo"] in {"segmento", "palavra_chave"} and len(evidencias) - antes < max_por_termo:
                if _sem_acento(termo_busca_atual.lower()) in _sem_acento(pagina.titulo.lower()):
                    evidencias.append(
                        {
                            "criterio_id": None,
                            "termo": termo_busca_atual,
                            "trecho": f"[título da página] {pagina.titulo}",
                            "url": url_origem,
                            "data_coleta": data_coleta,
                            "fonte_titulo": pagina.titulo,
                            "campo": "titulo",
                        }
                    )
        for evidencia in evidencias[antes:]:
            evidencia["criterio_id"] = criterio["id"]
    return evidencias


def hash_conteudo(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def extrair_entidades(html: str) -> list[str]:
    """Nomes/entidades citados na página (JSON-LD "name", og:site_name, h1).

    Heurística ampla: devolve candidatos com fonte implícita (a própria página);
    o chamador rotula como "citado na página", não como verificação.
    """
    entidades: list[str] = []

    def adicionar(valor: str) -> None:
        valor = re.sub(r"\s+", " ", valor).strip()
        if 2 < len(valor) <= 90 and re.search(r"[A-Za-zÀ-ú]", valor) and valor not in entidades:
            entidades.append(valor)

    m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html[:300_000], re.IGNORECASE | re.DOTALL)
    if m_h1:
        adicionar(re.sub(r"<[^>]+>", " ", m_h1.group(1)))
    aspas = "[\"']"
    padrao_og = (
        r"property=" + aspas + r"og:site_name" + aspas + r"[^>]*content=" + aspas + r"([^" + aspas + r"]+)"
    )
    padrao_og_alt = (
        r"content=" + aspas + r"([^" + aspas + r"]+)[^>]*property=" + aspas + r"og:site_name"
    )
    m_og = re.search(padrao_og, html[:100_000], re.IGNORECASE) or re.search(
        padrao_og_alt, html[:100_000], re.IGNORECASE
    )
    if m_og:
        adicionar(m_og.group(1))
    padrao_name = r'"name"\s*:\s*"([^"\\]{3,90})"'
    for m in re.finditer(padrao_name, html):
        adicionar(m.group(1))
        if len(entidades) >= 10:
            break
    return entidades
