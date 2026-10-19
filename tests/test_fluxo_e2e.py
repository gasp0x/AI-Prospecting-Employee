"""Fluxo ponta a ponta hermético: ICP → pesquisa (conectores simulados) → resultados → exportação.

Os conectores externos são substituídos por respostas de teste, mas TODO o
motor (tarefas, descoberta, coleta, evidências, pontuação, persistência,
exportação) é o código real de produção.
"""
import time

from fastapi.testclient import TestClient

from app import db
from app.adapters import sites
from app.adapters.websearch import BuscaBloqueada
from app.engine.research import executar_pesquisa  # noqa: F401 (garante import do módulo)
from app.main import app
from tests.conftest import HTML_EXEMPLO, aguardar

cliente = TestClient(app)

HTML_SITE = """
<html><head><title>Fisio Bem Estar — Fisioterapia em Campinas</title></head><body>
<p>A Fisio Bem Estar é uma clínica de fisioterapia em Campinas que atende convênios.</p>
<p>CNPJ: 19.131.243/0001-97</p></body></html>
"""

CANDIDATOS_FAKES = [
    {
        "chave": "node/1", "nome": "Fisio Bem Estar",
        "endereco": "Rua A, 100, Campinas, SP, 13010-000", "cidade": "Campinas", "uf": "SP",
        "localizacao": "Campinas/SP", "categoria": "physiotherapist",
        "site": None, "fonte_url": "https://www.openstreetmap.org/node/1", "lat": "-22.9", "lon": "-47.0",
    },
    {
        "chave": "node/2", "nome": "Clinica Exemplo",
        "endereco": "Rua B, 200, Campinas, SP", "cidade": "Campinas", "uf": "SP",
        "localizacao": "Campinas/SP", "categoria": "physiotherapist",
        "site": "https://clinicaexemplo.com.br/", "fonte_url": "https://www.openstreetmap.org/node/2", "lat": "-22.9", "lon": "-47.1",
    },
]


def _instalar_fakes(monkeypatch):
    from app.engine import research

    monkeypatch.setattr(research.nominatim, "buscar", lambda consulta, limite=12, somente_brasil=True: CANDIDATOS_FAKES)

    def busca_bloqueada(consulta, limite=8):
        raise BuscaBloqueada("motor de busca retornou bloqueio (HTTP 202, anti-bot/CAPTCHA)")

    monkeypatch.setattr(research.websearch, "buscar", busca_bloqueada)

    def coletar_site(url):
        if url == "https://clinicaexemplo.com.br/":
            return sites.ResultadoColeta(url=url, url_final=url, estado="ok", html=HTML_SITE)
        raise AssertionError(f"site inesperado: {url}")

    monkeypatch.setattr(research.sites, "coletar_site", coletar_site)

    def fake_cnpj(cnpj_formatado):
        return {
            "cnpj": "19131243000197", "razao_social": "EXEMPLO SERVICOS DE FISIOTERAPIA LTDA",
            "municipio": "Campinas", "uf": "SP", "cnae_descricao": "Clínicas de fisioterapia", "situacao": "Ativa",
        }

    monkeypatch.setattr(research.brasilapi, "consultar_cnpj", fake_cnpj)


def test_fluxo_completo_pesquisa_qualificacao_exportacao(banco_tmp, monkeypatch):
    _instalar_fakes(monkeypatch)

    # 1. cria o ICP pela interface
    resposta = cliente.post("/icp/novo", follow_redirects=False, data={"texto": "Clínicas de fisioterapia em Campinas (SP) que atendam convênios."})
    assert resposta.status_code == 303
    icp_id = int(resposta.headers["location"].rsplit("/", 1)[-1])

    # 2. inicia a pesquisa
    resposta = cliente.post(f"/icp/{icp_id}/pesquisa", follow_redirects=False, data={"maximo": "10"})
    assert resposta.status_code == 303
    tarefa_id = int(resposta.headers["location"].rsplit("/", 1)[-1])

    # 3. aguarda a conclusão (thread real do motor)
    assert aguardar(
        lambda: db.buscar_um("tarefa", tarefa_id)["estado"] == "concluida", timeout_s=15
    ), db.buscar_um("tarefa", tarefa_id)["estado"]

    # 4. empresas persistidas com pontuação e evidências reais do fluxo
    empresas = db.listar("empresa", where="icp_id = ?", parametros=(icp_id,))
    assert len(empresas) == 2
    por_nome = {e["nome"]: e for e in empresas}

    com_site = por_nome["Clinica Exemplo"]
    assert com_site["dominio"] == "clinicaexemplo.com.br"
    assert com_site["estado"] == "qualificada"
    evidencias = db.de_json(com_site["evidencias_json"])
    trechos = [e["trecho"] for e in evidencias]
    assert any("convênios" in t for t in trechos)  # evidência da página oficial
    assert any("CNAE" in t for t in trechos)       # enriquecimento CNPJ (site oficial)
    detalhe = db.de_json(com_site["pontuacao_json"])
    assert detalhe["pontuacao"] == 100.0  # todos os critérios corroborados

    sem_site = por_nome["Fisio Bem Estar"]
    assert sem_site["site"] is None  # busca bloqueada → sem site inventado
    # segmentos + localização comprovados só pela fonte de descoberta (parcial, 0,7×):
    # (3+3+2)*0,7 = 5,6 de 9,5; "convênios" ausente (busca bloqueada, sem página oficial)
    assert sem_site["pontuacao"] == 58.9

    # 5. limitação registrada no histórico com transparência
    eventos = [e["mensagem"] for e in db.listar("tarefa_evento", where="tarefa_id = ?", parametros=(tarefa_id,))]
    assert any("bloqueio" in m.lower() for m in eventos)

    # 6. gera rascunho e pede aprovação (vínculo com conteúdo exato)
    resposta = cliente.post(f"/empresa/{com_site['id']}/rascunho/gerar", follow_redirects=False)
    assert resposta.status_code == 303
    rascunho = db.listar("rascunho", where="empresa_id = ?", parametros=(com_site["id"],))[0]
    assert "Fisio Bem Estar" in rascunho["conteudo"]

    resposta = cliente.post(f"/empresa/{com_site['id']}/solicitar_envio", follow_redirects=False, data={"destinatario": ""})
    assert resposta.status_code == 303
    aprovacoes = db.listar("aprovacao", where="tipo_acao = 'criar_rascunho_mail'")
    assert aprovacoes and aprovacoes[0]["conteudo"] == rascunho["conteudo"]
    import hashlib

    assert aprovacoes[0]["conteudo_sha256"] == hashlib.sha256(rascunho["conteudo"].encode()).hexdigest()

    # 7. exportação CSV e JSON
    csv_texto = cliente.get(f"/exportar/empresas?icp={icp_id}&formato=csv").text
    assert "Fisio Bem Estar" in csv_texto and "Clinica Exemplo" in csv_texto
    import json

    dados = json.loads(cliente.get(f"/exportar/empresas?icp={icp_id}&formato=json").text)
    assert len(dados) == 2
    assert all("fontes" in e and "evidencias" in e for e in dados)

    # 8. resultados persistidos e reconsultáveis (recuperação após "reinício")
    empresa_pagina = cliente.get(f"/empresa/{com_site['id']}")
    assert empresa_pagina.status_code == 200
    assert "Clinica Exemplo" in empresa_pagina.text


def test_duplicatas_marcadas_por_dominio(banco_tmp, monkeypatch):
    _instalar_fakes(monkeypatch)
    resposta = cliente.post("/icp/novo", follow_redirects=False, data={"texto": "Clínicas de fisioterapia em Campinas (SP) que atendam convênios."})
    icp_id = int(resposta.headers["location"].rsplit("/", 1)[-1])
    cliente.post(f"/icp/{icp_id}/pesquisa", data={"maximo": "10"})
    assert aguardar(lambda: db.buscar_um("tarefa", db.listar("tarefa", limite=1)[0]["id"])["estado"] == "concluida")

    # mesma pesquisa com NOVO ICP: mesma empresa → segunda é duplicata
    resposta = cliente.post("/icp/novo", follow_redirects=False, data={"texto": "Outra busca de fisioterapia em Campinas."})
    icp2 = int(resposta.headers["location"].rsplit("/", 1)[-1])
    cliente.post(f"/icp/{icp2}/pesquisa", data={"maximo": "10"})
    assert aguardar(lambda: len(db.listar("tarefa", limite=10)) >= 2 and all(
        t["estado"] == "concluida" for t in db.listar("tarefa", limite=10)
    ))
    duplicatas = db.listar("empresa", where="estado = 'duplicata'")
    assert duplicatas, "esperava duplicatas na segunda rodada"
    assert all(d["duplicata_de"] for d in duplicatas)


def test_paginas_principais_respondem(banco_tmp):
    for caminho in ("/", "/resultados", "/aprovacoes", "/extensao", "/exportar", "/auditoria"):
        assert cliente.get(caminho).status_code == 200, caminho


def test_revisao_na_interface_nao_quebra_a_pesquisa(banco_tmp, monkeypatch):
    """Regressão do KeyError \'consulta\': ICP salvo sem a chave → pesquisa funciona.

    Reproduz o estado exato do ICP que quebrou em produção: critérios persistidos
    sem a chave `consulta` (versões antigas da rota de revisão). O motor deve
    enriquecer na carga e concluir a pesquisa. Observação: o TestClient desta
    versão de starlette/httpx não encaminha campos de formulário repetidos, então
    a persistência é feita direto no banco (a rota real foi verificada com curl).
    """
    _instalar_fakes(monkeypatch)

    resposta = cliente.post("/icp/novo", follow_redirects=False, data={"texto": "Academias em São Paulo (SP)."})
    icp_id = int(resposta.headers["location"].rsplit("/", 1)[-1])

    # critérios SEM `consulta`, como ficavam após a revisão pela interface (estado do bug)
    db.atualizar(
        "icp",
        icp_id,
        {
            "criterios_json": db.para_json([
                {"id": "c1", "tipo": "segmento", "termo": "academias", "peso": 3.0},
                {"id": "c2", "tipo": "localizacao", "termo": "São Paulo (SP)", "peso": 2.0},
            ]),
            "origem": "revisado",
        },
    )

    # o enriquecimento deriva os termos de busca do `termo`
    from app.engine.icp import enriquecer_criterios
    enriquecidos = enriquecer_criterios(db.de_json(db.buscar_um("icp", icp_id)["criterios_json"]))
    assert enriquecidos[0]["consulta"] == "academia"
    assert enriquecidos[1]["consulta"] == "São Paulo"

    resposta = cliente.post(f"/icp/{icp_id}/pesquisa", follow_redirects=False, data={"maximo": "10"})
    assert resposta.status_code == 303
    tarefa_id = int(resposta.headers["location"].rsplit("/", 1)[-1])
    assert aguardar(lambda: db.buscar_um("tarefa", tarefa_id)["estado"] == "concluida"), \
        db.buscar_um("tarefa", tarefa_id)["estado"]
    assert len(db.listar("empresa", where="icp_id = ?", parametros=(icp_id,))) == 2
