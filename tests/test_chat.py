"""Testes do cérebro conversacional (intents pt-BR → ações do motor)."""
from fastapi.testclient import TestClient

from app import db
from app.engine import chatbot
from app.main import app
from tests.conftest import aguardar

cliente = TestClient(app)


def test_ajuda_e_fallback(banco_tmp):
    respostas = chatbot.responder("ajuda")
    assert "funcionário de prospecção" in respostas[0].texto.lower()
    respostas = chatbot.responder("abcdefxyz sem sentido")
    assert "llm" in respostas[0].texto.lower() and "ajuda" in respostas[0].texto.lower()


def test_icp_via_chat_inicia_pesquisa_e_notifica_conclusao(banco_tmp, monkeypatch):
    from app.adapters import sites
    from app.adapters.websearch import BuscaBloqueada
    from app.engine import research

    candidato = {
        "chave": "node/9", "nome": "Fisio Chat Teste",
        "endereco": "Rua X, 10, Campinas, SP", "cidade": "Campinas", "uf": "SP",
        "localizacao": "Campinas/SP", "categoria": "physiotherapist",
        "site": None, "fonte_url": "https://www.openstreetmap.org/node/9", "lat": None, "lon": None,
    }
    monkeypatch.setattr(research.nominatim, "buscar", lambda consulta, limite=12, somente_brasil=True: [candidato])

    def bloqueada(consulta, limite=8):
        raise BuscaBloqueada("todos os motores bloqueados")

    monkeypatch.setattr(research.websearch, "buscar", bloqueada)

    # usuário fala em lingu natural; o agente cria ICP + inicia a pesquisa
    respostas = chatbot.responder("procure clínicas de fisioterapia em Campinas (SP) que atendam convênios")
    assert any("iniciada" in r.texto for r in respostas), respostas
    tarefa_id = respostas[0].tarefa_id
    assert tarefa_id is not None

    # o usuário recebe a notificação de conclusão no chat
    assert aguardar(lambda: db.buscar_um("tarefa", tarefa_id)["estado"] == "concluida", timeout_s=10)
    msgs = chatbot.puxar_notificacoes()
    assert any(f"#{tarefa_id}" in m.texto and "concluída" in m.texto for m in msgs)
    # não duplica na segunda chamada
    assert chatbot.puxar_notificacoes() == []


def test_rascunho_e_aprovacao_pelo_chat(banco_tmp, monkeypatch):
    from app.engine import approvals

    enviados = []
    monkeypatch.setattr(approvals.macos_apps, "notes_criar_nota", lambda titulo, corpo: {"id": "n1"})

    # prepara empresa com evidência
    icp_id = db.inserir("icp", {"texto_bruto": "t", "criterios_json": db.para_json([
        {"id": "c1", "tipo": "segmento", "termo": "fisioterapia", "peso": 3.0},
    ]), "proposta_valor": "crescer", "criado_em": db.agora(), "atualizado_em": db.agora()})
    empresa_id = db.inserir("empresa", {
        "icp_id": icp_id, "nome": "Clinica Alpha", "site": "https://alpha.com.br", "dominio": "alpha.com.br",
        "evidencias_json": db.para_json([{"criterio_id": "c1", "termo": "fisioterapia", "trecho": "clínica de fisioterapia", "url": "https://alpha.com.br", "data_coleta": db.agora(), "campo": "texto"}]),
        "fontes_json": "[]", "coletado_em": db.agora(), "pontuacao": 100.0,
        "pontuacao_json": db.para_json({"pontuacao": 100.0, "por_criterio": []}), "estado": "qualificada",
    })

    respostas = chatbot.responder("rascunho para clinica alpha")
    assert any("Rascunho para" in r.texto for r in respostas)
    assert "Clinica Alpha" in respostas[0].texto
    aprovacao_id = int(db.listar("aprovacao", where="tipo_acao = 'criar_rascunho_mail'")[0]["id"])

    # aprova pelo chat
    respostas = chatbot.responder(f"aprovar {aprovacao_id}")
    assert any("aprovada" in r.texto.lower() for r in respostas)
    assert aguardar(lambda: db.buscar_um("aprovacao", aprovacao_id)["estado"] == "executada")
    assert not enviados  # rascunho não envia e-mail


def test_status_resultados_exportar(banco_tmp):
    assert chatbot.responder("status")[0].texto
    assert "não tenho empresas" in chatbot.responder("resultados")[0].texto.lower()
    exportar = chatbot.responder("exportar")[0]
    assert any(a["url"].endswith("formato=csv") for a in exportar.acoes)


def test_api_chat_ponta_a_ponta(banco_tmp):
    resposta = cliente.post("/api/chat", json={"texto": "ajuda"})
    assert resposta.status_code == 200
    assert resposta.json()["respostas"][0]["texto"]
    historico = cliente.get("/api/chat/mensagens?depois=0").json()
    papeis = [m["papel"] for m in historico["mensagens"]]
    assert "usuario" in papeis and "agente" in papeis


def test_pagina_do_chat_responde(banco_tmp):
    pagina = cliente.get("/")
    assert pagina.status_code == 200
    assert "Funcionário de Prospecção" in pagina.text
    assert cliente.get("/painel").status_code == 200


def test_segmento_generico_duplicado_e_removido(banco_tmp):
    """'clínicas de fisioterapia' vira UM segmento (fisioterapia), não dois."""
    respostas = chatbot.responder("procure clínicas de fisioterapia em Campinas (SP), com site próprio")
    assert any("2 critério" in r.texto for r in respostas), respostas
    from app.engine.icp import extrair_criterios
    r = extrair_criterios("clínicas de fisioterapia em Campinas (SP)")
    segs = [c["termo"] for c in r["criterios"] if c["tipo"] == "segmento"]
    assert segs == ["fisioterapia"]


def test_limpar_chat(banco_tmp):
    cliente.post("/api/chat", json={"texto": "ajuda"})
    assert db.listar("chat_msg", limite=1)
    resposta = cliente.post("/api/chat/limpar")
    assert resposta.status_code == 200
    assert db.listar("chat_msg", limite=1) == []
