"""Teste do protocolo WebSocket da extensão do navegador (ponte local)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.adapters.browser import ponte_navegador
from app.main import app
from tests.conftest import aguardar

cliente = TestClient(app)


def test_ciclo_completo_pedido_leitura_aba(banco_tmp):
    with cliente.websocket_connect("/ws/extensao") as ws:
        ws.send_json({"ola": {"navegador": "Chrome", "perfil": "teste", "conectado_em": "agora"}})
        ola = ws.receive_json()
        assert "pong" in ola

        status = ponte_navegador.status()
        assert status["conectado"] is True
        assert status["navegador"] == "Chrome"

        # o agente cria um pedido de leitura de aba
        pedido_id = ponte_navegador.criar_pedido(
            "ler_aba", {"justificativa": "coletar resultado de busca"}
        )
        mensagem = ws.receive_json()
        assert mensagem["pedido"]["id"] == pedido_id
        assert mensagem["pedido"]["tipo"] == "ler_aba"

        # o usuário permite no popup → extensão responde com o conteúdo da aba
        ws.send_json(
            {
                "resposta": {
                    "id": pedido_id,
                    "ok": True,
                    "dados": {"url": "https://exemplo.com/", "titulo": "T", "texto": "conteúdo visível"},
                }
            }
        )
        assert aguardar(lambda: db.buscar_um("pedido_extensao", pedido_id)["estado"] == "atendida")
        resposta = db.de_json(db.buscar_um("pedido_extensao", pedido_id)["resposta_json"])
        assert resposta["url"] == "https://exemplo.com/"


def test_pedido_negado_pelo_usuario(banco_tmp):
    with cliente.websocket_connect("/ws/extensao") as ws:
        ws.send_json({"ola": {"navegador": "Chrome"}})
        ws.receive_json()
        pedido_id = ponte_navegador.criar_pedido("abrir_url", {"url": "https://exemplo.com/"})
        ws.receive_json()  # recebe o pedido
        ws.send_json({"resposta": {"id": pedido_id, "ok": False, "dados": {"erro": "negado pelo usuário"}}})
        assert aguardar(lambda: db.buscar_um("pedido_extensao", pedido_id)["estado"] == "negada")


def test_pedidos_pendentes_entregues_ao_conectar(banco_tmp):
    # pedido criado SEM extensão conectada → fica pendente (backlog)
    pedido_id = ponte_navegador.criar_pedido("ler_aba", {"justificativa": "atrasado"})
    assert db.buscar_um("pedido_extensao", pedido_id)["estado"] == "pendente"

    with cliente.websocket_connect("/ws/extensao") as ws:
        ws.send_json({"ola": {"navegador": "Edge"}})
        mensagem = ws.receive_json()
        assert mensagem["pedido"]["id"] == pedido_id  # backlog entregue na conexão


def test_aguardar_resposta_expira_pedido(banco_tmp):
    pedido_id = ponte_navegador.criar_pedido("ler_aba", {"justificativa": "ninguém conectado"})
    resposta = ponte_navegador.aguardar_resposta(pedido_id, timeout_s=0.4, intervalo_s=0.1)
    assert resposta is None
    assert db.buscar_um("pedido_extensao", pedido_id)["estado"] == "expirada"


def test_token_invalido_recusa_conexao(banco_tmp, monkeypatch):
    from app.config import settings

    object.__setattr__(settings, "token_extensao", "segredo")
    try:
        with pytest.raises(Exception):
            with cliente.websocket_connect("/ws/extensao"):
                pass  # sem token → fechado pelo servidor
    finally:
        object.__setattr__(settings, "token_extensao", "")
