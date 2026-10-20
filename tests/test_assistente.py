"""Testes do assistente geral (habilidades sem LLM + pergunta aberta com LLM)."""
from app import db
from app.engine import assistente, chatbot
from tests.conftest import aguardar


def test_calculo_porcentagem_e_expressao(banco_tmp):
    r = chatbot.responder("quanto é 15% de 240")
    assert "36" in r[0].texto
    r = chatbot.responder("calcula 12*7+3")
    assert "87" in r[0].texto
    assert chatbot.responder("calcula delete from usuarios") == [] or "erro" not in r[0].texto.lower()


def test_calculo_rejeita_codigo(banco_tmp):
    r = chatbot.responder("calcula __import__('os').system('ls')")
    # não executa nada: cai no fluxo honesto de pergunta aberta
    assert len(r) == 1 and "__import__" not in r[0].texto


def test_hora_e_data(banco_tmp):
    r = chatbot.responder("que horas são?")
    assert "Agora são" in r[0].texto
    r = chatbot.responder("que dia é hoje")
    assert "/" in r[0].texto


def test_lembrete_cria_entrega_e_lista(banco_tmp):
    r = chatbot.responder("lembre-me de ligar para o João em 30 minutos")
    assert "Vou te lembrar" in r[0].texto and "João" in r[0].texto
    lembretes = db.listar("lembrete", where="estado = 'pendente'")
    assert len(lembretes) == 1
    assert "Lembretes ativos" in chatbot.responder("meus lembretes")[0].texto

    # vence o lembrete e verifica a entrega como mensagem do agente no chat
    db.atualizar("lembrete", lembretes[0]["id"], {"quando_utc": "2020-01-01T00:00:00Z"})
    entregues = assistente.verificar_lembretes()
    assert len(entregues) == 1
    mensagens = db.listar("chat_msg", where="papel = 'agente'", ordem="id DESC", limite=1)
    assert "Lembrete" in mensagens[0]["texto"] and "João" in mensagens[0]["texto"]
    assert db.buscar_um("lembrete", lembretes[0]["id"])["estado"] == "entregue"


def test_cotacao_com_fonte(banco_tmp, monkeypatch):
    import httpx

    fixture = {"USD BRL": {"bid": "5.4321", "create_date": "2026-10-19 18:00:00"},
               "EUR BRL": {"bid": "6.3210", "create_date": "2026-10-19 18:00:00"}}

    class Resposta:
        status_code = 200

        def raise_for_status(self): pass

        def json(self): return fixture

    monkeypatch.setattr(assistente.httpx, "get", lambda *a, **k: Resposta())
    r = chatbot.responder("cotação do dólar")
    assert "R$ 5.4321" in r[0].texto and "Dólar" in r[0].texto


def test_clima_com_geocode_e_open_meteo(banco_tmp, monkeypatch):
    respostas_http = [
        type("R1", (), {"json": staticmethod(lambda: [{"lat": "-22.9", "lon": "-47.0", "name": "Campinas"}]), "status_code": 200})(),
        type("R2", (), {"json": staticmethod(lambda: {"current_weather": {"temperature": 25.0, "weathercode": 2, "windspeed": 8.0}}), "status_code": 200})(),
    ]
    monkeypatch.setattr(assistente.httpx, "get", lambda *a, **k: respostas_http.pop(0))
    r = chatbot.responder("clima em Campinas")
    assert "25.0" in r[0].texto or "25" in r[0].texto
    assert "Campinas" in r[0].texto and "parcialmente nublado" in r[0].texto


def test_resumo_extrativo_de_pagina(banco_tmp, monkeypatch):
    from app.adapters.sites import ResultadoColeta

    html = ("<html><head><title>Clínica Exemplo</title>"
            "<meta name='description' content='Clínica de fisioterapia em Campinas.'></head>"
            "<body><p>" + "texto longo enough para parágrafo. " * 12 + "</p></body></html>")
    monkeypatch.setattr(assistente.sites, "coletar_site",
                        lambda url: ResultadoColeta(url=url, url_final=url, estado="ok", html=html))
    r = chatbot.responder("resume https://clinica.com.br")
    assert "Clínica Exemplo" in r[0].texto
    assert "extrativo" in r[0].texto.lower()


def test_anotar_cria_aprovacao_nao_grava_direto(banco_tmp):
    r = chatbot.responder("anote: reunião com o cliente na sexta às 14h")
    assert "Apple Notes" in r[0].texto and "aprovar" in r[0].texto.lower()
    aprovacoes = db.listar("aprovacao", where="tipo_acao = 'criar_nota_notes'")
    assert aprovacoes and aprovacoes[0]["estado"] == "pendente"


def test_pergunta_aberta_com_llm_e_sem_llm(banco_tmp, monkeypatch):
    monkeypatch.setattr(assistente, "llm_disponivel", lambda: True)
    monkeypatch.setattr(assistente, "completar_texto", lambda system, user: "Machado de Assis foi um escritor brasileiro.")
    import app.engine.chatbot as chatbot_mod
    monkeypatch.setattr(chatbot_mod, "llm_disponivel", lambda: True)
    r = chatbot.responder("quem foi Machado de Assis?")
    assert "Machado" in r[0].texto

    monkeypatch.setattr(assistente, "llm_disponivel", lambda: False)
    r = chatbot.responder("escreva um poema sobre vendas")
    assert "LLM" in r[0].texto and "ajuda" in r[0].texto


def test_prospeccao_continua_funcionando_apos_assistente(banco_tmp, monkeypatch):
    r = chatbot.responder("procure dentistas em Curitiba (PR)")
    assert any("iniciada" in x.texto for x in r)
    assert aguardar(lambda: True)  # sanidade


def test_buscar_info_estabelecimento_por_nome_e_cidade(banco_tmp, monkeypatch):
    from app.adapters import nominatim

    candidatos = [{
        "chave": "node/1", "nome": "Tony Veículos",
        "endereco": "Av. São João 1000, São José dos Campos, SP",
        "cidade": "São José dos Campos", "uf": "SP", "localizacao": "São José dos Campos/SP",
        "categoria": "car_dealer", "site": None, "telefone": "(12) 3232-0000",
        "fonte_url": "https://www.openstreetmap.org/node/1", "lat": None, "lon": None,
    }]
    monkeypatch.setattr(nominatim, "buscar", lambda consulta, limite=6, somente_brasil=True: candidatos)
    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", lambda consulta, limite=5: [])

    pergunta = ('me dê algumas informações, como endereço e numero de telefone das lojas '
                '"tony veiculos" na cidade de São José dos Campos')
    r = chatbot.responder(pergunta)
    assert "Tony Veículos" in r[0].texto
    assert "Av. São João 1000" in r[0].texto
    assert "(12) 3232-0000" in r[0].texto
    assert "openstreetmap.org" in r[0].texto


def test_buscar_info_sem_mapa_cai_para_web(banco_tmp, monkeypatch):
    from app.adapters import nominatim

    monkeypatch.setattr(nominatim, "buscar", lambda consulta, limite=6, somente_brasil=True: [])
    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", lambda consulta, limite=5: [
        {"url": "https://doctoralia.com.br/zezim", "titulo": "Zezin Motos — Doctoralia"},
    ])
    r = chatbot.responder('telefone das lojas "zezin motos" em Sorocaba')
    assert "na **web**" in r[0].texto or "encontrei na" in r[0].texto.lower()
    assert "Zezin Motos" in r[0].texto


def test_buscar_info_sem_mapa_sem_web_e_honesto(banco_tmp, monkeypatch):
    from app.adapters import nominatim
    from app.adapters.websearch import BuscaBloqueada

    monkeypatch.setattr(nominatim, "buscar", lambda consulta, limite=6, somente_brasil=True: [])
    def bloqueada(consulta, limite=5):
        raise BuscaBloqueada("todos os motores bloqueados")
    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", bloqueada)
    r = chatbot.responder('telefone das lojas "zezin motos" em Sorocaba')
    assert "não encontrei" in r[0].texto.lower()
    assert "bloqueando" in r[0].texto.lower()


def test_buscar_info_enriquece_com_telefone_do_site(banco_tmp, monkeypatch):
    from app.adapters import nominatim
    from app.adapters.sites import ResultadoColeta

    candidatos = [{
        "chave": "node/2", "nome": "Clinica Beta",
        "endereco": "Rua Y, 20, Campinas, SP", "cidade": "Campinas", "uf": "SP",
        "localizacao": "Campinas/SP", "categoria": "clinic", "site": "https://beta.com.br",
        "telefone": None, "fonte_url": "https://www.openstreetmap.org/node/2", "lat": None, "lon": None,
    }]
    monkeypatch.setattr(nominatim, "buscar", lambda consulta, limite=6, somente_brasil=True: candidatos)
    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", lambda consulta, limite=5: [])
    html = ("<html><head><title>Beta</title></head><body>Contato (19) 99999-8888 "
            "contato@beta.com.br</body></html>")
    monkeypatch.setattr(assistente.sites, "coletar_site",
                        lambda url: ResultadoColeta(url=url, url_final=url, estado="ok", html=html))
    r = chatbot.responder("informações da clinica beta em campinas")
    assert "(19) 99999-8888" in r[0].texto
    assert "contato@beta.com.br" in r[0].texto


def test_consulta_singular_cardiologista_nao_vira_pesquisa(banco_tmp, monkeypatch):
    """'procure um cardiologista que atenda no santa casa em SJC' = consulta direta."""
    from app.adapters import nominatim

    consultas_usadas = []

    def fake_buscar(consulta, limite=6, somente_brasil=True):
        consultas_usadas.append(consulta)
        return [{
            "chave": "node/7", "nome": "Dr. Carlos - Cardiologista",
            "endereco": "Rua Centro 50, São José dos Campos, SP",
            "cidade": "São José dos Campos", "uf": "SP", "localizacao": "São José dos Campos/SP",
            "categoria": "doctor", "site": None, "telefone": "(12) 3600-0000",
            "fonte_url": "https://www.openstreetmap.org/node/7", "lat": None, "lon": None,
        }]

    monkeypatch.setattr(nominatim, "buscar", fake_buscar)
    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", lambda consulta, limite=5: [])
    r = chatbot.responder("procure um cardiologista que atenda no santa casa em São José dos campos")
    # NÃO iniciou pesquisa de ICP
    assert not any("iniciada" in x.texto for x in r)
    # retornou a consulta com o cardiologista
    assert any("Cardiologista" in x.texto for x in r)
    assert any("cardiologista" in c.lower() for c in consultas_usadas)
    # qualificador "atenda no santa casa" é declarado como não-filtrável na fonte
    assert any("atenda no santa casa" in x.texto for x in r)


def test_icp_extracao_limpa_para_frases_com_qualificador(banco_tmp):
    from app.engine.icp import extrair_criterios
    r = extrair_criterios("procure um cardiologista que atenda no santa casa em São José dos campos")
    termos = [c["termo"] for c in r["criterios"]]
    assert "cardiologista" in termos
    assert "casa" not in termos            # não vira segmento genérico
    assert "no santa casa" not in termos   # preposição limpa no critério palavra-chave
    assert any("santa casa" in t for t in termos)  # mas o hospital fica como palavra-chave


def test_lembrete_no_app_lembretes_cria_aprovacao(banco_tmp, monkeypatch):
    from app.engine import approvals

    scripts = []
    monkeypatch.setattr(approvals.macos_apps, "executar_osascript", lambda script, timeout_s=25: scripts.append(script) or "ok")

    r = chatbot.responder("lembre-me no Lembretes de pagar o boleto amanhã às 9h")
    assert "Lembretes" in r[0].texto and "aprovar" in r[0].texto.lower()
    aprovacao = db.listar("aprovacao", where="tipo_acao = 'criar_lembrete_reminders'")[0]
    params = db.de_json(aprovacao["params_json"])
    assert "pagar o boleto" in params["titulo"]
    assert db.de_json(aprovacao["params_json"])["quando_utc"]

    # aprovar executa o AppleScript do Reminders com as propriedades certas
    approvals.decidir(aprovacao["id"], aprovar=True)
    assert aguardar(lambda: db.buscar_um("aprovacao", aprovacao["id"])["estado"] == "executada")
    assert any("Reminders" in s and "pagar o boleto" in s and "due date" in s for s in scripts)


def test_agenda_calendario_cria_aprovacao_e_executa(banco_tmp, monkeypatch):
    from app.engine import approvals

    scripts = []
    monkeypatch.setattr(approvals.macos_apps, "executar_osascript", lambda script, timeout_s=25: scripts.append(script) or "ok")

    r = chatbot.responder("agende reunião com o cliente amanhã às 14h")
    assert "Calendário" in r[0].texto and "14:00" in r[0].texto
    aprovacao = db.listar("aprovacao", where="tipo_acao = 'criar_evento_calendar'")[0]
    params = db.de_json(aprovacao["params_json"])
    assert "reunião com o cliente" in params["titulo"].lower()

    approvals.decidir(aprovacao["id"], aprovar=True)
    assert aguardar(lambda: db.buscar_um("aprovacao", aprovacao["id"])["estado"] == "executada")
    assert any("Calendar" in s and "make new event" in s and "reuni" in s.lower() for s in scripts)


def test_email_real_pelo_chat_com_aprovacao(banco_tmp, monkeypatch):
    from app.engine import approvals

    enviados = []
    monkeypatch.setattr(approvals.macos_apps, "mail_enviar_email",
                        lambda para, assunto, corpo: enviados.append((para, assunto, corpo)) or {"enviado": True})

    r = chatbot.responder("envie e-mail para cliente@x.com com assunto Proposta dizendo: Segue a proposta em anexo.")
    assert "ENVIAR" in r[0].texto.upper() and "cliente@x.com" in r[0].texto
    aprovacao = db.listar("aprovacao", where="tipo_acao = 'enviar_email_mail'")[0]
    assert aprovacao["conteudo"] == "Segue a proposta em anexo."

    approvals.decidir(aprovacao["id"], aprovar=True)
    assert aguardar(lambda: db.buscar_um("aprovacao", aprovacao["id"])["estado"] == "executada")
    assert enviados == [("cliente@x.com", "Proposta", "Segue a proposta em anexo.")]


def test_interpretar_quando_formatos():
    from datetime import datetime

    from app.engine.assistente import interpretar_quando

    base = datetime(2026, 10, 20, 10, 0)
    quando, resto = interpretar_quando("reunião amanhã às 14h", base)
    assert (quando.day, quando.hour, quando.minute) == (21, 14, 0) and "amanhã" not in resto
    quando, resto = interpretar_quando("dentista dia 25/10 às 9h30", base)
    assert (quando.day, quando.month, quando.hour, quando.minute) == (25, 10, 9, 30)
    quando, resto = interpretar_quando("ligar em 30 minutos", base)
    assert (quando - base).total_seconds() == 1800
    quando, resto = interpretar_quando("reunião sexta às 10h", base)  # 20/10/2026 é terça
    assert quando.weekday() == 4 and quando.day == 23


def test_busca_geral_para_pedidos_de_nomes(banco_tmp, monkeypatch):
    """A frase exata que falhou duas vezes agora vai para a busca geral na web."""
    consultas = []

    def fake_detalhado(consulta, limite=6):
        consultas.append(consulta)
        return [{"url": "https://tuasaude.com/x", "titulo": "Cardiologistas que atendem Santa Casa SJC"}]

    monkeypatch.setattr(assistente.websearch, "buscar_detalhado", fake_detalhado)
    monkeypatch.setattr(assistente, "llm_disponivel", lambda: False)

    frase = ('preciso que você me de nomes de cardiologistas que trabalham no '
             '"Santa Casa" em São José dos Campos')
    r = chatbot.responder(frase)
    # não foi para LLM nem para pesquisa de prospecção
    assert "LLM" not in r[0].texto and "iniciada" not in r[0].texto
    # foi para a web, com consulta limpa
    assert len(consultas) == 1
    assert "cardiologistas" in consultas[0].lower()
    assert "santa casa" in consultas[0].lower()
    assert "são josé" in consultas[0].lower()
    assert not consultas[0].lower().startswith("de ")
    assert "web" in r[0].texto.lower() and "tuasaude.com" in r[0].texto


def test_prospeccao_plural_de_negocio_continua_research(banco_tmp):
    r = chatbot.responder("procure clínicas de fisioterapia em Campinas (SP)")
    assert any("iniciada" in x.texto for x in r)
