"""Testes das aprovações: vínculo com conteúdo exato e execução autorizada."""
import hashlib

import pytest

from app import db
from app.engine import approvals
from tests.conftest import aguardar


def test_hash_vincula_conteudo(banco_tmp, monkeypatch):
    chamadas = []

    def fake_acao(titulo, corpo):
        chamadas.append((titulo, corpo))
        return {"id": "nota-123", "titulo": titulo}

    monkeypatch.setattr(approvals.macos_apps, "notes_criar_nota", fake_acao)
    conteudo = "Conteúdo EXATO da nota."
    aprovacao_id = approvals.solicitar("criar_nota_notes", "resumo", conteudo, {"titulo": "T"})
    registro = db.buscar_um("aprovacao", aprovacao_id)
    assert registro["conteudo_sha256"] == hashlib.sha256(conteudo.encode()).hexdigest()

    resultado = approvals.decidir(aprovacao_id, aprovar=True)
    assert aguardar(
        lambda: db.buscar_um("aprovacao", aprovacao_id)["estado"] == "executada"
    )
    assert chamadas == [("T", conteudo)]
    tarefa = db.buscar_um("tarefa", resultado["tarefa_id"])
    assert tarefa["estado"] == "concluida"


def test_conteudo_alterado_apos_aprovacao_impede_execucao(banco_tmp):
    aprovacao_id = approvals.solicitar("criar_nota_notes", "resumo", "conteudo original", {"titulo": "T"})
    # simula adulteração do conteúdo no banco DEPOIS do hash e ANTES da aprovação
    db.atualizar("aprovacao", aprovacao_id, {"conteudo": "conteudo ADULTERADO"})
    approvals.decidir(aprovacao_id, aprovar=True)
    assert aguardar(lambda: any(
        t["estado"] == "falha" for t in db.listar("tarefa", limite=5)
    ))
    tarefa = next(t for t in db.listar("tarefa", limite=5) if t["estado"] == "falha")
    assert "hash não confere" in tarefa["erro"]


def test_envio_real_passa_por_aprovacao_e_executa(banco_tmp, monkeypatch):
    """Envio real de e-mail: executor recebe destinatário/assunto/conteúdo exatos."""
    enviados = []

    def fake_enviar(para, assunto, corpo):
        enviados.append((para, assunto, corpo))
        return {"id": "msg-1", "para": para, "enviado": True}

    monkeypatch.setattr(approvals.macos_apps, "mail_enviar_email", fake_enviar)
    conteudo = "Mensagem aprovada para envio real."
    aprovacao_id = approvals.solicitar(
        "enviar_email_mail",
        "resumo",
        conteudo,
        {"destinatario": "contato@empresa.com.br", "assunto": "Abordagem"},
    )
    approvals.decidir(aprovacao_id, aprovar=True)
    assert aguardar(lambda: db.buscar_um("aprovacao", aprovacao_id)["estado"] == "executada")
    assert enviados == [("contato@empresa.com.br", "Abordagem", conteudo)]


def test_envio_real_sem_destinatario_falha(banco_tmp, monkeypatch):
    enviados = []

    def fake_enviar(para, assunto, corpo):
        enviados.append((para, assunto, corpo))
        return {}

    monkeypatch.setattr(approvals.macos_apps, "mail_enviar_email", fake_enviar)
    aprovacao_id = approvals.solicitar(
        "enviar_email_mail", "resumo", "conteudo", {"destinatario": "", "assunto": "Abordagem"}
    )
    approvals.decidir(aprovacao_id, aprovar=True)
    assert aguardar(lambda: any(
        t["estado"] == "falha" for t in db.listar("tarefa", limite=5)
    ))
    assert enviados == []


def test_negar_nao_executa(banco_tmp, monkeypatch):
    chamado = {"vezes": 0}

    def fake_acao(titulo, corpo):
        chamado["vezes"] += 1
        return {}

    monkeypatch.setattr(approvals.macos_apps, "notes_criar_nota", fake_acao)
    aprovacao_id = approvals.solicitar("criar_nota_notes", "resumo", "conteudo", {})
    approvals.decidir(aprovacao_id, aprovar=False)
    assert db.buscar_um("aprovacao", aprovacao_id)["estado"] == "negada"
    import time

    time.sleep(0.3)
    assert chamado["vezes"] == 0


def test_decidir_duas_vezes_falha(banco_tmp):
    aprovacao_id = approvals.solicitar("criar_nota_notes", "resumo", "conteudo", {})
    approvals.decidir(aprovacao_id, aprovar=False)
    with pytest.raises(ValueError):
        approvals.decidir(aprovacao_id, aprovar=True)
