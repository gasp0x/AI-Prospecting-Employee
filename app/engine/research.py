"""Execução da pesquisa de prospecção: descobrir → coletar evidências → qualificar.

Pipeline por tarefa:
1. Carrega o ICP e seus critérios revisados.
2. Descobre candidatos em fontes públicas autorizadas:
   - Nominatim/OpenStreetMap (POIs reais, com endereço e às vezes site);
   - busca web DuckDuckGo HTML (com detecção de bloqueio anti-bot).
3. Para cada candidato: consulta robots.txt, coleta a página inicial com
   tentativas/backoff, detecta CAPTCHA/bloqueio e extrai evidências e metadados
   (CNPJ → enriquecimento via BrasilAPI/dados abertos da Receita).
4. Pontua a aderência ao ICP de forma explicável e persiste tudo com fontes.
5. Marca duplicatas (mesmo domínio já conhecido).

Cancelamento, prazo e tentativas são verificados nos pontos seguros do loop.
Nada é inventado: candidato sem evidência é mantido com pontuação baixa e
critérios "ausentes", nunca com dados fabricados.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from ..adapters import brasilapi, nominatim, sites, websearch
from .. import db
from . import scoring
from . import icp as mod_icp
from .evidence import coletar_evidencias, extrair_pagina, hash_conteudo

_DOMINIOS_IRRELEVANTES = {
    "facebook.com", "instagram.com", "linkedin.com", "google.com", "google.com.br",
    "youtube.com", "twitter.com", "x.com", "tiktok.com", "wa.me", "whatsapp.com",
    "mercadolivre.com", "mercadolivre.com.br", "amazon.com.br", "ifood.com.br",
    # motores de busca e links de anúncio (nunca são o site da empresa)
    "duckduckgo.com", "lite.duckduckgo.com", "bing.com", "yahoo.com", "ecosia.org",
    "search.brave.com", "startpage.com", "mojeek.com", "r.jina.ai",
}

# palavras genéricas que não identificam domínio oficial
_STOPWORDS_NOME = {
    "centro", "clinica", "especializada", "aplicada", "fisioterapia", "empresa",
    "servicos", "limitada", "studio", "escola", "instituto", "consultoria",
}


def dominio_de(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else (host or None)
    except ValueError:
        return None


def dominio_corresponde_ao_nome(dominio: str | None, nome: str) -> bool:
    """Heurística: o domínio contém algum token distintivo do nome da empresa?

    Usada para separar SITE OFICIAL de PÁGINA RELACIONADA (marketplace,
    diretórios, notícias) — a página relacionada continua servindo como fonte
    de evidência, mas não é apresentada como site da empresa.
    """
    if not dominio:
        return False
    from .evidence import _sem_acento  # uso interno entre módulos do motor

    partes = [p for p in dominio.lower().split(".") if p not in {"com", "br", "net", "org", "ecom"}]
    nucleo = "".join(partes)
    tokens = {
        t for t in _sem_acento(nome.lower()).replace("-", " ").replace("&", " ").split()
        if len(t) >= 4 and t not in _STOPWORDS_NOME
    }
    return any(t in nucleo for t in tokens)


def _com_tentativas(ctx, descricao: str, fn, tentativas: int, backoff_s: float = 1.5):
    """Executa `fn` com tentativas limitadas e backoff; re-levanta BloqueioDetectado sem retry."""
    ultima_erro: Exception | None = None
    for tentativa in range(tentativas + 1):
        ctx.check()
        try:
            return fn()
        except sites.BloqueioDetectado:
            raise
        except Exception as erro:  # noqa: BLE001
            ultima_erro = erro
            if tentativa < tentativas:
                ctx.evento(
                    f"Falha transitória em {descricao} (tentativa {tentativa + 1}/{tentativas + 1}): {erro}",
                    nivel="aviso",
                    etapa="coleta",
                )
                time.sleep(backoff_s * (tentativa + 1))
    raise ultima_erro  # type: ignore[misc]


def executar_pesquisa(ctx, params: dict) -> dict:
    icp_id = int(params["icp_id"])
    registro_icp = db.buscar_um("icp", icp_id)
    if registro_icp is None:
        raise ValueError(f"ICP {icp_id} não encontrado")
    # enriquece na carga: ICPs salvos antes da revisão pela interface podem não ter `consulta`
    criterios = mod_icp.enriquecer_criterios(db.de_json(registro_icp["criterios_json"], []))
    if not criterios:
        raise ValueError("ICP sem critérios de pesquisa — revise o ICP antes de iniciar.")

    resumo_bloqueios: list[str] = []
    ctx.evento(
        f"Pesquisa iniciada com {len(criterios)} critérios.",
        etapa="planejamento",
        dados={"criterios": [c["termo"] for c in criterios]},
    )
    ctx.progresso(3)

    # ---------- 1. Descoberta ----------
    candidatos: list[dict] = []
    consultas = params.get("consultas") or _consultas_do_icp(criterios)
    if not consultas:
        raise ValueError("Nenhuma consulta de pesquisa pôde ser derivada dos critérios.")

    for i, consulta in enumerate(consultas):
        ctx.check()
        ctx.evento(f"Buscando candidatos: “{consulta}” (Nominatim/OpenStreetMap).", etapa="descoberta")
        try:
            lugares = _com_tentativas(
                ctx, f"Nominatim “{consulta}”", lambda c=consulta: nominatim.buscar(c), tentativas=1
            )
        except nominatim.FonteBloqueada as erro:
            resumo_bloqueios.append(f"Nominatim bloqueou a consulta “{consulta}”: {erro}")
            ctx.evento(f"Nominatim indisponível para “{consulta}”: {erro}", nivel="aviso", etapa="descoberta")
            continue
        for lugar in lugares:
            if any(c["chave"] == lugar["chave"] for c in candidatos):
                continue
            candidatos.append(lugar)
        ctx.progresso(5 + int(15 * (i + 1) / len(consultas)))

    ctx.evento(f"Descoberta concluída: {len(candidatos)} candidatos únicos.", etapa="descoberta")

    limite = int(params.get("maximo_empresas") or 20)
    candidatos = candidatos[:limite]

    # ---------- 2. Investigação ----------
    criadas = 0
    qualificadas = 0
    bloqueios_site = 0
    erros_site = 0

    for indice, candidato in enumerate(candidatos):
        ctx.check()
        rotulo = candidato["nome"]
        progresso_item = 20 + int(65 * indice / max(1, len(candidatos)))
        ctx.progresso(progresso_item)

        evidencias: list[dict] = []
        fontes: list[dict] = [
            {"tipo": "fonte_inicial", "url": candidato["fonte_url"], "descricao": "Registro público OpenStreetMap/Nominatim", "data_coleta": db.agora()}
        ]
        sinais_inferencia: list[dict] = []
        cnpjs: list[str] = []
        pagina_info: dict = {}

        # Evidência direta da fonte inicial (endereço/categoria do OSM são fatos daquela fonte)
        if candidato.get("endereco"):
            for cid_local in [c["id"] for c in criterios if c["tipo"] == "localizacao"]:
                evidencias.append({
                    "criterio_id": cid_local,
                    "termo": "endereço",
                    "trecho": f"Endereço registrado: {candidato['endereco']}",
                    "url": candidato["fonte_url"],
                    "data_coleta": db.agora(),
                    "fonte_titulo": "OpenStreetMap (via Nominatim)",
                    "campo": "fonte_osm",
                })
        if candidato.get("categoria"):
            for cid_segmento in [c["id"] for c in criterios if c["tipo"] == "segmento"]:
                evidencias.append({
                    "criterio_id": cid_segmento,
                    "termo": "categoria",
                    "trecho": f"Categoria registrada na fonte: {candidato['categoria']}",
                    "url": candidato["fonte_url"],
                    "data_coleta": db.agora(),
                    "fonte_titulo": "OpenStreetMap (via Nominatim)",
                    "campo": "fonte_osm",
                })

        site = candidato.get("site")
        site_oficial = bool(site)  # site registrado na fonte de descoberta (OSM) é tratado como oficial
        pagina_relacionada: str | None = None
        if not site:
            achado = _procurar_site_via_busca(ctx, candidato, resumo_bloqueios)
            if achado:
                if dominio_corresponde_ao_nome(dominio_de(achado), candidato["nome"]):
                    site, site_oficial = achado, True
                else:
                    # página de terceiro que menciona a empresa: fonte válida de
                    # evidência, mas NÃO é o site oficial dela
                    pagina_relacionada = achado

        alvo = site or pagina_relacionada
        if alvo:
            rotulo_alvo = f"site oficial ({alvo})" if site_oficial else f"página relacionada ({alvo})"
            ctx.evento(f"Coletando {rotulo_alvo} de {rotulo}.", etapa="coleta")
            try:
                resultado = _com_tentativas(
                    ctx, f"site {alvo}", lambda a=alvo: sites.coletar_site(a), tentativas=2
                )
            except sites.BloqueioDetectado as erro:
                bloqueios_site += 1
                resumo_bloqueios.append(f"{alvo}: {erro}")
                ctx.evento(
                    f"Acesso bloqueado a {alvo} — interrompendo a coleta deste site. ({erro})",
                    nivel="aviso",
                    etapa="coleta",
                )
                resultado = None
            except Exception as erro:  # noqa: BLE001
                erros_site += 1
                ctx.evento(f"Não foi possível coletar {alvo}: {type(erro).__name__}: {erro}", nivel="aviso", etapa="coleta")
                resultado = None

            if resultado and resultado.estado == "ok":
                pagina = extrair_pagina(resultado.url_final, resultado.html)
                novas = coletar_evidencias(
                    pagina, criterios, resultado.url_final, db.agora(),
                    max_por_termo=3,
                )
                evidencias.extend(novas)
                cnpjs = pagina.cnpjs if site_oficial else []  # CNPJ de página de terceiro não pertence à empresa
                pagina_info = {
                    "titulo": pagina.titulo,
                    "descricao": pagina.meta_description,
                    "telefones": pagina.telefones,
                    "ceps": pagina.ceps,
                    "emails": pagina.emails,
                }
                if pagina.titulo:
                    fontes.append({
                        "tipo": "pagina" if site_oficial else "pagina_relacionada",
                        "url": resultado.url_final,
                        "descricao": ("Página oficial" if site_oficial else "Página de terceiro que menciona a empresa") + f" ({pagina.titulo})",
                        "data_coleta": db.agora(),
                    })
            elif resultado and resultado.estado == "robots":
                resumo_bloqueios.append(f"{alvo}: coleta desallow por robots.txt")
                ctx.evento(f"{alvo} proíbe coleta automatizada via robots.txt — respeitado.", nivel="aviso", etapa="coleta")
                bloqueios_site += 1

        # Enriquecimento por CNPJ (dados abertos da Receita via BrasilAPI)
        if cnpjs:
            cnpj = cnpjs[0]
            try:
                ctx.check()
                dados = brasilapi.consultar_cnpj(cnpj)
            except Exception as erro:  # noqa: BLE001
                ctx.evento(f"Enriquecimento CNPJ falhou para {cnpj}: {erro}", nivel="aviso", etapa="coleta")
                dados = None
            if dados:
                url_cnpj = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
                cid_segmento = _criterio_segmento(criterios)
                cid_local = _criterio_localizacao(criterios)
                if dados["cnae_descricao"] and cid_segmento:
                    evidencias.append({
                        "criterio_id": cid_segmento,
                        "termo": "CNAE",
                        "trecho": (
                            f"Razão social “{dados['razao_social']}” — CNAE: {dados['cnae_descricao']} "
                            f"(fonte: dados abertos da Receita Federal via BrasilAPI, CNPJ {cnpj})"
                        ),
                        "url": url_cnpj,
                        "data_coleta": db.agora(),
                        "fonte_titulo": f"BrasilAPI CNPJ {cnpj}",
                        "campo": "fonte_cnpj",
                    })
                if dados["municipio"] and cid_local:
                    evidencias.append({
                        "criterio_id": cid_local,
                        "termo": "sede",
                        "trecho": (
                            f"Sede registrada na Receita Federal: {dados['municipio']}/{dados['uf']} "
                            f"(via BrasilAPI, CNPJ {cnpj})"
                        ),
                        "url": url_cnpj,
                        "data_coleta": db.agora(),
                        "fonte_titulo": f"BrasilAPI CNPJ {cnpj}",
                        "campo": "fonte_cnpj",
                    })
                fontes.append({
                    "tipo": "cnpj",
                    "url": url_cnpj,
                    "descricao": f"Dados cadastrais (Receita Federal via BrasilAPI): {dados['razao_social']} — {dados['municipio']}/{dados['uf']}",
                    "data_coleta": db.agora(),
                })

        # Inferências etiquetadas (nunca viram fato)
        if site and dominio_de(site) and dominio_de(site).endswith(".br"):
            cid_local = _criterio_localizacao(criterios)
            if cid_local:
                sinais_inferencia.append({
                    "criterio_id": cid_local,
                    "detalhe": f"Domínio {dominio_de(site)} é brasileiro (.br) — sugere atuação no Brasil.",
                    "fonte": site,
                })

        # ---------- 3. Qualificação + persistência ----------
        pontuacao = scoring.pontuar(
            criterios, evidencias, sinais_inferencia, url_fonte_inicial=candidato["fonte_url"]
        )
        dominio = dominio_de(site) if site else None
        duplicata_de = (
            _encontrar_duplicata(dominio, candidato["nome"], candidato.get("localizacao"))
            if (dominio or candidato["nome"])
            else None
        )
        estado = "duplicata" if duplicata_de else ("qualificada" if pontuacao["pontuacao"] >= 40 else "nova")

        empresa_id = db.inserir("empresa", {
            "icp_id": icp_id,
            "tarefa_id": ctx.tarefa_id,
            "nome": candidato["nome"],
            "site": site,
            "dominio": dominio,
            "segmento": candidato.get("categoria"),
            "localizacao": candidato.get("endereco"),
            "fontes_json": db.para_json(fontes),
            "evidencias_json": db.para_json(evidencias),
            "coletado_em": db.agora(),
            "hash_conteudo": hash_conteudo("".join(e["trecho"] for e in evidencias)),
            "pontuacao": pontuacao["pontuacao"],
            "pontuacao_json": db.para_json(pontuacao),
            "estado": estado,
            "duplicata_de": duplicata_de,
        })
        criadas += 1
        if estado == "qualificada":
            qualificadas += 1
        ctx.evento(
            f"{rotulo}: pontuação {pontuacao['pontuacao']:.1f} ({estado})" + (" — duplicata de empresa já conhecida." if duplicata_de else ""),
            etapa="qualificacao",
            dados={"empresa_id": empresa_id, "evidencias": len(evidencias)},
        )

    # ---------- 4. Resumo ----------
    ctx.progresso(95)
    resumo = {
        "icp_id": icp_id,
        "candidatos": len(candidatos),
        "empresas_criadas": criadas,
        "qualificadas": qualificadas,
        "bloqueios_site": bloqueios_site,
        "erros_site": erros_site,
        "bloqueios": resumo_bloqueios,
    }
    if resumo_bloqueios:
        ctx.evento(
            "Limitações de acesso registradas durante a execução: " + " | ".join(resumo_bloqueios[:5]),
            nivel="aviso",
            etapa="resumo",
        )
    ctx.evento(
        f"Concluído: {criadas} empresas registradas, {qualificadas} qualificadas (≥40 pontos), "
        f"{bloqueios_site} bloqueios e {erros_site} erros de coleta.",
        etapa="resumo",
    )
    return resumo


# ---------- helpers ----------

def _consultas_do_icp(criterios: list[dict]) -> list[str]:
    from .icp import consultas_pesquisa

    return consultas_pesquisa(criterios)


def _criterio_segmento(criterios: list[dict]) -> str | None:
    for c in criterios:
        if c["tipo"] == "segmento":
            return c["id"]
    return None


def _criterio_localizacao(criterios: list[dict]) -> str | None:
    for c in criterios:
        if c["tipo"] == "localizacao":
            return c["id"]
    return None


def _procurar_site_via_busca(ctx, candidato: dict, resumo_bloqueios: list[str]) -> str | None:
    """Tenta achar o site oficial do candidato via busca web. Trata bloqueio com transparência."""
    consulta = f"{candidato['nome']} {candidato.get('cidade') or ''}".strip()
    if not consulta:
        return None
    try:
        ctx.check()
        resultados = websearch.buscar(consulta, limite=3)
    except websearch.BuscaBloqueada as erro:
        resumo_bloqueios.append(f"busca web bloqueada para “{consulta}”: {erro}")
        return None
    except Exception as erro:  # noqa: BLE001
        ctx.evento(f"Busca web indisponível para “{consulta}”: {erro}", nivel="aviso", etapa="descoberta")
        return None
    for url in resultados:
        d = dominio_de(url)
        if d and not any(d == irrel or d.endswith("." + irrel) for irrel in _DOMINIOS_IRRELEVANTES):
            return url
    return None


def _encontrar_duplicata(dominio: str | None, nome: str, localizacao: str | None = None) -> int | None:
    """Domínio igual ⇒ duplicata. Nome igual só é duplicata se a localização também bate
    (POIs genéricos como "Academia" existem dezenas de vezes na mesma cidade)."""
    if dominio:
        existente = db.listar(
            "empresa", where="dominio = ? AND estado != 'duplicata'", parametros=(dominio,), limite=1
        )
        if existente:
            return int(existente[0]["id"])
    similares = db.listar(
        "empresa",
        where="LOWER(nome) = LOWER(?) AND estado != 'duplicata'",
        parametros=(nome,),
        limite=50,
    )
    for existente in similares:
        if (existente["localizacao"] or "") == (localizacao or ""):
            return int(existente["id"])
    return None
