"""Fixtures compartilhadas: banco temporário e fábricas de teste."""
from __future__ import annotations

import time

import pytest

from app.config import settings


@pytest.fixture()
def banco_tmp(tmp_path, monkeypatch):
    """Aponta o banco do app para um arquivo temporário por teste."""
    caminho = tmp_path / "teste.sqlite3"
    object.__setattr__(settings, "caminho_banco", caminho)
    from app import db

    db.init_schema(caminho)
    yield caminho
    object.__setattr__(settings, "caminho_banco", tmp_path / "descartado.sqlite3")


def aguardar(condicao, timeout_s: float = 10.0, intervalo_s: float = 0.1):
    """Espera uma condição virar verdadeira (para tarefas em thread)."""
    prazo = time.monotonic() + timeout_s
    while time.monotonic() < prazo:
        if condicao():
            return True
        time.sleep(intervalo_s)
    return False


HTML_EXEMPLO = """
<html><head><title>Clinica Exemplo — Fisioterapia em Campinas</title>
<meta name="description" content="Clínica de fisioterapia em Campinas SP, convênios e agendamento online.">
</head><body>
<h1>Clinica Exemplo</h1>
<p>Somos uma clínica de fisioterapia em Campinas, atendemos convênios desde 2010.</p>
<p>CNPJ: 19.131.243/0001-97 Telefone (19) 3232-1010. CEP 13010-000</p>
<p>fisioterapia esportiva e ortopédica com equipe própria.</p>
</body></html>
"""
