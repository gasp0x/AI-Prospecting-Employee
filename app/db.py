"""Camada de dados: SQLite local, schema e utilitários de acesso.

Decisões:
- SQLite (stdlib `sqlite3`): persistência local em arquivo único, sem serviço
  externo, sem compilação nativa — roda igual em qualquer Mac Intel.
- Modo WAL para leitura concorrente entre a interface e as tarefas em thread.
- Uma conexão por operação (barato em SQLite) evita problemas de thread.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS icp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  texto_bruto TEXT NOT NULL,
  criterios_json TEXT NOT NULL DEFAULT '[]',
  proposta_valor TEXT NOT NULL DEFAULT '',
  assinatura TEXT NOT NULL DEFAULT '',
  origem TEXT NOT NULL DEFAULT 'automatico',
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tarefa (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'pendente',
  icp_id INTEGER REFERENCES icp(id),
  aprovacao_id INTEGER,
  params_json TEXT NOT NULL DEFAULT '{}',
  progresso INTEGER NOT NULL DEFAULT 0,
  resultado_json TEXT,
  erro TEXT,
  criado_em TEXT NOT NULL,
  iniciado_em TEXT,
  concluido_em TEXT
);

CREATE TABLE IF NOT EXISTS tarefa_evento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tarefa_id INTEGER NOT NULL REFERENCES tarefa(id),
  ts TEXT NOT NULL,
  nivel TEXT NOT NULL DEFAULT 'info',
  etapa TEXT NOT NULL DEFAULT '',
  mensagem TEXT NOT NULL,
  dados_json TEXT
);

CREATE TABLE IF NOT EXISTS empresa (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icp_id INTEGER NOT NULL REFERENCES icp(id),
  tarefa_id INTEGER REFERENCES tarefa(id),
  nome TEXT NOT NULL,
  site TEXT,
  dominio TEXT,
  segmento TEXT,
  localizacao TEXT,
  fontes_json TEXT NOT NULL DEFAULT '[]',
  evidencias_json TEXT NOT NULL DEFAULT '[]',
  coletado_em TEXT NOT NULL,
  hash_conteudo TEXT,
  pontuacao REAL,
  pontuacao_json TEXT,
  estado TEXT NOT NULL DEFAULT 'nova',
  duplicata_de INTEGER REFERENCES empresa(id)
);
CREATE INDEX IF NOT EXISTS idx_empresa_dominio ON empresa(dominio);
CREATE INDEX IF NOT EXISTS idx_empresa_icp ON empresa(icp_id);

CREATE TABLE IF NOT EXISTS rascunho (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  empresa_id INTEGER NOT NULL REFERENCES empresa(id),
  conteudo TEXT NOT NULL,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  aprovacao_id INTEGER
);

CREATE TABLE IF NOT EXISTS aprovacao (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo_acao TEXT NOT NULL,
  tarefa_id INTEGER,
  resumo TEXT NOT NULL,
  conteudo TEXT NOT NULL,
  conteudo_sha256 TEXT NOT NULL,
  params_json TEXT NOT NULL DEFAULT '{}',
  estado TEXT NOT NULL DEFAULT 'pendente',
  criado_em TEXT NOT NULL,
  decidida_em TEXT,
  executada_em TEXT,
  resultado_json TEXT
);

CREATE TABLE IF NOT EXISTS pedido_extensao (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo TEXT NOT NULL,
  params_json TEXT NOT NULL DEFAULT '{}',
  estado TEXT NOT NULL DEFAULT 'pendente',
  resposta_json TEXT,
  criado_em TEXT NOT NULL,
  decidida_em TEXT
);

CREATE TABLE IF NOT EXISTS lembrete (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  texto TEXT NOT NULL,
  quando_utc TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'pendente',
  criado_em TEXT NOT NULL,
  entregue_em TEXT
);

CREATE TABLE IF NOT EXISTS chat_msg (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  papel TEXT NOT NULL,
  texto TEXT NOT NULL,
  acoes_json TEXT,
  tarefa_id INTEGER,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auditoria (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ator TEXT NOT NULL,
  acao TEXT NOT NULL,
  detalhes_json TEXT NOT NULL DEFAULT '{}'
);
"""


def agora() -> str:
    """Timestamp UTC ISO-8601 com segundos (formato canônico do banco)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conectar(banco: Path | None = None) -> sqlite3.Connection:
    caminho = banco or settings.caminho_banco
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(banco: Path | None = None) -> None:
    with conectar(banco) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def transacao(banco: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = conectar(banco)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- helpers de leitura/escrita ----------

def inserir(tabela: str, valores: dict[str, Any], banco: Path | None = None) -> int:
    colunas = ", ".join(valores)
    placeholders = ", ".join("?" for _ in valores)
    with transacao(banco) as conn:
        cur = conn.execute(
            f"INSERT INTO {tabela} ({colunas}) VALUES ({placeholders})",
            tuple(valores.values()),
        )
        return int(cur.lastrowid)


def atualizar(tabela: str, registro_id: int, valores: dict[str, Any], banco: Path | None = None) -> None:
    atribuicoes = ", ".join(f"{c} = ?" for c in valores)
    with transacao(banco) as conn:
        conn.execute(
            f"UPDATE {tabela} SET {atribuicoes} WHERE id = ?",
            tuple(valores.values()) + (registro_id,),
        )


def buscar_um(tabela: str, registro_id: int, banco: Path | None = None) -> sqlite3.Row | None:
    with conectar(banco) as conn:
        return conn.execute(f"SELECT * FROM {tabela} WHERE id = ?", (registro_id,)).fetchone()


def listar(
    tabela: str,
    where: str = "",
    parametros: tuple = (),
    ordem: str = "id DESC",
    limite: int = 200,
    banco: Path | None = None,
) -> list[sqlite3.Row]:
    sql = f"SELECT * FROM {tabela}"
    if where:
        sql += f" WHERE {where}"
    if ordem:
        sql += f" ORDER BY {ordem}"
    sql += f" LIMIT {int(limite)}"
    with conectar(banco) as conn:
        return conn.execute(sql, parametros).fetchall()


def para_json(dados: Any) -> str:
    return json.dumps(dados, ensure_ascii=False, sort_keys=True)


def de_json(texto: str | None, padrao: Any = None) -> Any:
    if not texto:
        return padrao
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return padrao


def registrar_auditoria(ator: str, acao: str, detalhes: dict[str, Any], banco: Path | None = None) -> None:
    inserir(
        "auditoria",
        {"ts": agora(), "ator": ator, "acao": acao, "detalhes_json": para_json(detalhes)},
        banco=banco,
    )
