"""Interface web em português do Brasil (páginas server-rendered + formulários)."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path

from .. import db
from ..adapters.browser import ponte_navegador
from ..config import settings
from ..db import buscar_um, listar
from ..engine import approvals, drafting, icp as mod_icp, research
from ..engine.tasks import ROTULOS_ESTADO, gerenciador_tarefas

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["rotulo_estado"] = lambda e: ROTULOS_ESTADO.get(e, e)

router = APIRouter()


def _ctx(request: Request, **extras) -> dict:
    base = {
        "request": request,
        "extensao": ponte_navegador.status(),
        "titulo": "AI Prospecting Employee",
    }
    base.update(extras)
    return base


# ---------------- Painel ----------------

@router.get("/", response_class=HTMLResponse)
def painel(request: Request):
    tarefas = listar("tarefa", ordem="id DESC", limite=8)
    empresas_recentes = listar("empresa", where="estado != 'duplicata'", ordem="pontuacao DESC", limite=5)
    registros_icp = listar("icp", ordem="id DESC", limite=5)
    icps = [{**i, "n_criterios": len(db.de_json(i["criterios_json"], []))} for i in registros_icp]
    return templates.TemplateResponse(
        request,
        "painel.html",
        _ctx(request, tarefas=tarefas, empresas=empresas_recentes, icps=icps),
    )


# ---------------- ICP ----------------

@router.post("/icp/novo")
def novo_icp(request: Request, texto: str = Form(...)):
    if not texto.strip():
        return RedirectResponse("/", status_code=303)
    extraido = mod_icp.extrair_criterios(texto)
    icp_id = db.inserir(
        "icp",
        {
            "texto_bruto": texto.strip(),
            "criterios_json": db.para_json(extraido["criterios"]),
            "proposta_valor": texto.strip(),
            "assinatura": settings.assinatura,
            "origem": "automatico",
            "criado_em": db.agora(),
            "atualizado_em": db.agora(),
        },
    )
    db.registrar_auditoria("usuario", "icp_criado", {"icp_id": icp_id, "criterios": len(extraido["criterios"])})
    return RedirectResponse(f"/icp/{icp_id}", status_code=303)


@router.get("/icp/{icp_id}", response_class=HTMLResponse)
def ver_icp(request: Request, icp_id: int):
    registro = db.buscar_um("icp", icp_id)
    if registro is None:
        return RedirectResponse("/", status_code=303)
    criterios = db.de_json(registro["criterios_json"], [])
    tarefas = listar("tarefa", where="icp_id = ?", parametros=(icp_id,), ordem="id DESC")
    return templates.TemplateResponse(
        request,
        "icp.html",
        _ctx(request, icp=registro, criterios=criterios, tarefas=tarefas, alertas=_alertas_icp(criterios)),
    )


def _alertas_icp(criterios: list[dict]) -> list[str]:
    alertas = []
    if not any(c["tipo"] == "segmento" for c in criterios):
        alertas.append("Sem critério de segmento — a pesquisa ficará muito ampla.")
    if not any(c["tipo"] == "localizacao" for c in criterios):
        alertas.append("Sem critério de localização — resultados de qualquer lugar.")
    return alertas


@router.post("/icp/{icp_id}/criterios")
def salvar_criterios(
    request: Request,
    icp_id: int,
    tipo: list[str] = Form(default=[]),
    termo: list[str] = Form(default=[]),
    peso: list[str] = Form(default=[]),
    proposta_valor: str = Form(""),
    assinatura: str = Form(""),
):
    registro = db.buscar_um("icp", icp_id)
    if registro is None:
        return RedirectResponse("/", status_code=303)
    criterios = []
    for i in range(min(len(tipo), len(termo))):
        termo_i = termo[i].strip()
        if not termo_i:
            continue
        try:
            peso_f = max(0.5, min(5.0, float(peso[i]) if i < len(peso) and peso[i] else 1.0))
        except ValueError:
            peso_f = 1.0
        criterios.append({"id": f"c{i + 1}", "tipo": tipo[i], "termo": termo_i, "peso": peso_f})
    db.atualizar(
        "icp",
        icp_id,
        {
            "criterios_json": db.para_json(mod_icp.enriquecer_criterios(criterios)),
            "proposta_valor": proposta_valor.strip(),
            "assinatura": assinatura.strip(),
            "origem": "revisado",
            "atualizado_em": db.agora(),
        },
    )
    db.registrar_auditoria("usuario", "icp_criterios_revisados", {"icp_id": icp_id, "total": len(criterios)})
    return RedirectResponse(f"/icp/{icp_id}", status_code=303)


@router.post("/icp/{icp_id}/pesquisa")
def iniciar_pesquisa(request: Request, icp_id: int, maximo: str = Form("")):
    registro = db.buscar_um("icp", icp_id)
    if registro is None:
        return RedirectResponse("/", status_code=303)
    criterios = db.de_json(registro["criterios_json"], [])
    if not criterios:
        return RedirectResponse(f"/icp/{icp_id}", status_code=303)
    params = {"icp_id": icp_id}
    if maximo.strip().isdigit():
        params["maximo_empresas"] = int(maximo)
    tarefa_id = gerenciador_tarefas.criar(tipo="pesquisa", params=params, icp_id=icp_id)
    gerenciador_tarefas.iniciar(tarefa_id, research.executar_pesquisa)
    db.registrar_auditoria("usuario", "pesquisa_iniciada", {"icp_id": icp_id, "tarefa_id": tarefa_id})
    return RedirectResponse(f"/tarefa/{tarefa_id}", status_code=303)


# ---------------- Tarefas ----------------

@router.get("/tarefa/{tarefa_id}", response_class=HTMLResponse)
def ver_tarefa(request: Request, tarefa_id: int):
    tarefa = db.buscar_um("tarefa", tarefa_id)
    if tarefa is None:
        return RedirectResponse("/", status_code=303)
    eventos = listar("tarefa_evento", where="tarefa_id = ?", parametros=(tarefa_id,), ordem="id ASC", limite=500)
    resultado = db.de_json(tarefa["resultado_json"], {})
    return templates.TemplateResponse(
        request,
        "tarefa.html",
        _ctx(request, tarefa=tarefa, eventos=eventos, resultado=resultado, rotulos=ROTULOS_ESTADO),
    )


@router.post("/tarefa/{tarefa_id}/cancelar")
def cancelar_tarefa(request: Request, tarefa_id: int):
    gerenciador_tarefas.cancelar(tarefa_id)
    db.registrar_auditoria("usuario", "tarefa_cancelada", {"tarefa_id": tarefa_id})
    return RedirectResponse(f"/tarefa/{tarefa_id}", status_code=303)


# ---------------- Resultados ----------------

@router.get("/resultados", response_class=HTMLResponse)
def resultados(request: Request, icp: int = 0):
    filtros = "estado != 'duplicata'"
    parametros: tuple = ()
    if icp:
        filtros += " AND icp_id = ?"
        parametros = (icp,)
    registros = listar("empresa", where=filtros, parametros=parametros, ordem="pontuacao DESC", limite=200)
    empresas = [
        {
            "id": e["id"], "nome": e["nome"], "site": e["site"], "dominio": e["dominio"],
            "localizacao": e["localizacao"], "pontuacao": e["pontuacao"], "estado": e["estado"],
            "duplicata_de": e["duplicata_de"],
            "n_evidencias": len(db.de_json(e["evidencias_json"], [])),
        }
        for e in registros
    ]
    icps = listar("icp", ordem="id DESC")
    return templates.TemplateResponse(
        request,
        "resultados.html",
        _ctx(request, empresas=empresas, icps=icps, icp_sel=icp),
    )


@router.get("/empresa/{empresa_id}", response_class=HTMLResponse)
def ver_empresa(request: Request, empresa_id: int):
    empresa = db.buscar_um("empresa", empresa_id)
    if empresa is None:
        return RedirectResponse("/resultados", status_code=303)
    pontuacao = db.de_json(empresa["pontuacao_json"], {})
    evidencias = db.de_json(empresa["evidencias_json"], [])
    fontes = db.de_json(empresa["fontes_json"], [])
    rascunho = db.listar("rascunho", where="empresa_id = ?", parametros=(empresa_id,), ordem="id DESC", limite=1)
    rascunho = rascunho[0] if rascunho else None
    icp = db.buscar_um("icp", empresa["icp_id"])
    return templates.TemplateResponse(
        request,
        "empresa.html",
        _ctx(
            request,
            empresa=empresa,
            pontuacao=pontuacao,
            evidencias=evidencias,
            fontes=fontes,
            rascunho=rascunho,
            icp=icp,
        ),
    )


@router.post("/empresa/{empresa_id}/rascunho/gerar")
def gerar_rascunho(request: Request, empresa_id: int):
    empresa = db.buscar_um("empresa", empresa_id)
    if empresa is None:
        return RedirectResponse("/resultados", status_code=303)
    icp = db.buscar_um("icp", empresa["icp_id"])
    evidencias = db.de_json(empresa["evidencias_json"], [])
    texto, _usou_llm = drafting.redigir_assistido(
        empresa["nome"],
        empresa["localizacao"] or "",
        evidencias,
        icp["proposta_valor"] if icp else "",
        (icp["assinatura"] if icp else "") or settings.assinatura,
    )
    db.inserir(
        "rascunho",
        {"empresa_id": empresa_id, "conteudo": texto, "criado_em": db.agora(), "atualizado_em": db.agora()},
    )
    db.registrar_auditoria("agente", "rascunho_gerado", {"empresa_id": empresa_id})
    return RedirectResponse(f"/empresa/{empresa_id}#rascunho", status_code=303)


@router.post("/empresa/{empresa_id}/rascunho/salvar")
def salvar_rascunho(request: Request, empresa_id: int, conteudo: str = Form(...)):
    existente = db.listar("rascunho", where="empresa_id = ?", parametros=(empresa_id,), ordem="id DESC", limite=1)
    if existente:
        db.atualizar("rascunho", int(existente[0]["id"]), {"conteudo": conteudo, "atualizado_em": db.agora()})
    else:
        db.inserir("rascunho", {"empresa_id": empresa_id, "conteudo": conteudo, "criado_em": db.agora(), "atualizado_em": db.agora()})
    return RedirectResponse(f"/empresa/{empresa_id}#rascunho", status_code=303)


@router.post("/empresa/{empresa_id}/solicitar_envio")
def solicitar_envio(request: Request, empresa_id: int, destinatario: str = Form("")):
    empresa = db.buscar_um("empresa", empresa_id)
    if empresa is None:
        return RedirectResponse("/resultados", status_code=303)
    existente = db.listar("rascunho", where="empresa_id = ?", parametros=(empresa_id,), ordem="id DESC", limite=1)
    if not existente:
        return RedirectResponse(f"/empresa/{empresa_id}", status_code=303)
    rascunho = existente[0]
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_rascunho_mail",
        resumo=f"Criar RASCUNHO de e-mail no Apple Mail para a empresa “{empresa['nome']}” (não envia automaticamente).",
        conteudo=rascunho["conteudo"],
        params={
            "destinatario": destinatario.strip(),
            "assunto": f"{empresa['nome']} — abordagem comercial",
            "rascunho_id": rascunho["id"],
            "empresa_id": empresa_id,
        },
    )
    db.registrar_auditoria("usuario", "envio_solicitado", {"empresa_id": empresa_id, "aprovacao_id": aprovacao_id})
    return RedirectResponse("/aprovacoes", status_code=303)


@router.post("/empresa/{empresa_id}/solicitar_envio_real")
def solicitar_envio_real(request: Request, empresa_id: int, destinatario: str = Form(...)):
    """Solicita ENVIO REAL do rascunho pelo Mail — exige aprovação com hash do conteúdo."""
    empresa = db.buscar_um("empresa", empresa_id)
    if empresa is None:
        return RedirectResponse("/resultados", status_code=303)
    existente = db.listar("rascunho", where="empresa_id = ?", parametros=(empresa_id,), ordem="id DESC", limite=1)
    if not existente:
        return RedirectResponse(f"/empresa/{empresa_id}", status_code=303)
    destinatario = destinatario.strip()
    if "@" not in destinatario or "." not in destinatario.split("@")[-1]:
        return templates.TemplateResponse(
            request,
            "erro.html",
            _ctx(request, mensagem=f"Destinatário inválido: “{destinatario}”. Informe um e-mail válido."),
            status_code=400,
        )
    rascunho = existente[0]
    aprovacao_id = approvals.solicitar(
        tipo_acao="enviar_email_mail",
        resumo=(
            f"ENVIAR DE VERDADE o e-mail abaixo pelo Apple Mail para {destinatario} "
            f"(empresa “{empresa['nome']}”). Ao aprovar, a mensagem sai da sua caixa — não há como desfazer."
        ),
        conteudo=rascunho["conteudo"],
        params={
            "destinatario": destinatario,
            "assunto": f"{empresa['nome']} — abordagem comercial",
            "rascunho_id": rascunho["id"],
            "empresa_id": empresa_id,
        },
    )
    db.registrar_auditoria("usuario", "envio_real_solicitado", {"empresa_id": empresa_id, "aprovacao_id": aprovacao_id, "destinatario": destinatario})
    return RedirectResponse("/aprovacoes", status_code=303)


@router.post("/empresa/{empresa_id}/descartar")
def descartar_empresa(request: Request, empresa_id: int):
    db.atualizar("empresa", empresa_id, {"estado": "descartada"})
    db.registrar_auditoria("usuario", "empresa_descartada", {"empresa_id": empresa_id})
    return RedirectResponse("/resultados", status_code=303)


# ---------------- Aprovações ----------------

@router.get("/aprovacoes", response_class=HTMLResponse)
def aprovacoes(request: Request):
    pendentes = listar("aprovacao", where="estado = 'pendente'", ordem="id DESC")
    decididas = listar(
        "aprovacao",
        where="estado != 'pendente'",
        ordem="id DESC",
        limite=20,
    )
    return templates.TemplateResponse(
        request,
        "aprovacoes.html",
        _ctx(request, pendentes=pendentes, decididas=decididas),
    )


@router.post("/aprovacoes/{aprovacao_id}/decidir")
def decidir_aprovacao(request: Request, aprovacao_id: int, acao: str = Form(...)):
    try:
        resultado = approvals.decidir(aprovacao_id, aprovar=(acao == "aprovar"))
    except ValueError as erro:
        return templates.TemplateResponse(
            request,
            "erro.html",
            _ctx(request, mensagem=str(erro)),
            status_code=400,
        )
    if resultado.get("tarefa_id"):
        return RedirectResponse(f"/tarefa/{resultado['tarefa_id']}", status_code=303)
    return RedirectResponse("/aprovacoes", status_code=303)


# ---------------- Ação local no macOS (demonstração/uso direto) ----------------

@router.post("/acao-local/notes")
def acao_notes(
    request: Request,
    titulo: str = Form(...),
    corpo: str = Form(...),
):
    aprovacao_id = approvals.solicitar(
        tipo_acao="criar_nota_notes",
        resumo=f"Criar nota no Apple Notes: “{titulo.strip()}”",
        conteudo=corpo,
        params={"titulo": titulo.strip()},
    )
    return RedirectResponse("/aprovacoes", status_code=303)


# ---------------- Navegador (extensão) ----------------

@router.get("/extensao", response_class=HTMLResponse)
def pagina_extensao(request: Request):
    pedidos = listar("pedido_extensao", ordem="id DESC", limite=20)
    return templates.TemplateResponse(
        request,
        "extensao.html",
        _ctx(request, pedidos=pedidos, porta=settings.porta, token_configurado=bool(settings.token_extensao)),
    )


@router.post("/extensao/ler-aba")
def pedir_leitura_aba(request: Request, justificativa: str = Form("")):
    pedido_id = ponte_navegador.criar_pedido(
        "ler_aba",
        {"justificativa": justificativa.strip() or "Coleta de conteúdo da aba ativa para prospecção"},
    )
    db.registrar_auditoria("usuario", "pedido_leitura_aba", {"pedido_id": pedido_id})
    return RedirectResponse("/extensao", status_code=303)


# ---------------- Exportação ----------------

@router.get("/exportar", response_class=HTMLResponse)
def pagina_exportar(request: Request):
    icps = listar("icp", ordem="id DESC")
    contagens = {
        int(i["id"]): db.listar("empresa", where="icp_id = ? AND estado != 'duplicata'", parametros=(i["id"],), limite=1000)
        for i in icps
    }
    return templates.TemplateResponse(
        request,
        "exportar.html",
        _ctx(request, icps=icps, contagens={k: len(v) for k, v in contagens.items()}),
    )


@router.get("/exportar/empresas")
def exportar_empresas(request: Request, icp: int = 0, formato: str = "csv"):
    where = "estado != 'duplicata'"
    parametros: tuple = ()
    if icp:
        where += " AND icp_id = ?"
        parametros = (icp,)
    empresas = listar("empresa", where=where, parametros=parametros, ordem="pontuacao DESC", limite=5000)
    if formato == "json":
        dados = []
        for e in empresas:
            dados.append(
                {
                    "id": e["id"],
                    "nome": e["nome"],
                    "site": e["site"],
                    "dominio": e["dominio"],
                    "segmento": e["segmento"],
                    "localizacao": e["localizacao"],
                    "pontuacao": e["pontuacao"],
                    "estado": e["estado"],
                    "coletado_em": e["coletado_em"],
                    "fontes": db.de_json(e["fontes_json"], []),
                    "evidencias": db.de_json(e["evidencias_json"], []),
                    "pontuacao_detalhe": db.de_json(e["pontuacao_json"], {}),
                }
            )
        return Response(
            content=json.dumps(dados, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="empresas_icp{icp or "todos"}.json"'},
        )
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(["id", "nome", "site", "dominio", "segmento", "localizacao", "pontuacao", "estado", "coletado_em", "fontes", "total_evidencias"])
    for e in empresas:
        fontes = " | ".join(f["url"] for f in db.de_json(e["fontes_json"], []) if f.get("url"))
        evidencias = db.de_json(e["evidencias_json"], [])
        escritor.writerow(
            [e["id"], e["nome"], e["site"] or "", e["dominio"] or "", e["segmento"] or "", e["localizacao"] or "", e["pontuacao"], e["estado"], e["coletado_em"], fontes, len(evidencias)]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="empresas_icp{icp or "todos"}.csv"'},
    )


# ---------------- Auditoria ----------------

@router.get("/auditoria", response_class=HTMLResponse)
def auditoria(request: Request):
    registros = listar("auditoria", ordem="id DESC", limite=200)
    return templates.TemplateResponse(request, "auditoria.html", _ctx(request, registros=registros))
