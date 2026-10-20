"""Configuração central do AI Prospecting Employee.

Lê variáveis de ambiente de um arquivo `.env` na raiz do projeto (formato
CHAVE=valor, uma por linha, sem dependência externa) e expõe um objeto
`settings` imutável. Credenciais ficam SEMPRE fora do código e do repositório:
apenas `.env.example` é versionado.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_dotenv(caminho: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if not caminho.exists():
        return valores
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


@dataclass(frozen=True)
class Config:
    # Servidor local
    host: str = "127.0.0.1"
    porta: int = 8734
    caminho_ws_extensao: str = "/ws/extensao"

    # Persistência
    diretorio_dados: Path = BASE_DIR / "data"
    caminho_banco: Path = BASE_DIR / "data" / "aipe.sqlite3"

    # Pesquisa
    agente_usuario: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AIProspectingEmployee/0.1"
    )
    maximo_empresas: int = 20          # limite de candidatos investigados por tarefa
    timeout_tarefa_s: int = 480        # prazo total de uma tarefa de pesquisa
    timeout_busca_s: int = 20          # timeout por requisição a fontes
    timeout_fetch_s: int = 15          # timeout por fetch de site
    tentativas_fetch: int = 2          # tentativas extras por fetch (backoff)
    maximo_trechos_por_termo: int = 3  # evidências coletadas por critério/página
    limite_nota_minima: float = 40.0   # pontuação mínima para "qualificada"

    # Extensão do navegador (opcional: token compartilhado WS)
    token_extensao: str = ""

    # Ação local no macOS
    assinatura: str = ""

    # Canal Telegram opcional (bot via @BotFather)
    token_telegram: str = ""

    # OpenClaw opcional (cérebro generalista "por baixo dos panos")
    openclaw_bin: str = ""

    # LLM opcional (endpoint compatível com OpenAI). Vazio = modo determinístico.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_modelo: str = ""

    extras: dict[str, str] = field(default_factory=dict)


def carregar_config() -> Config:
    env = _carregar_dotenv(BASE_DIR / ".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith("AIPE_") or k in _CHAVES_LLM})

    def pegar(chave: str, padrao: str = "") -> str:
        return env.get(chave, padrao) or padrao

    diretorio_dados = Path(pegar("AIPE_DIR_DADOS", str(BASE_DIR / "data")))
    config = Config(
        host=pegar("AIPE_HOST", "127.0.0.1"),
        porta=int(pegar("AIPE_PORTA", "8734")),
        caminho_ws_extensao=pegar("AIPE_WS_EXTENSAO", "/ws/extensao"),
        diretorio_dados=diretorio_dados,
        caminho_banco=diretorio_dados / "aipe.sqlite3",
        agente_usuario=pegar(
            "AIPE_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AIProspectingEmployee/0.1",
        ),
        maximo_empresas=int(pegar("AIPE_MAX_EMPRESAS", "20")),
        timeout_tarefa_s=int(pegar("AIPE_TIMEOUT_TAREFA_S", "480")),
        timeout_busca_s=int(pegar("AIPE_TIMEOUT_BUSCA_S", "20")),
        timeout_fetch_s=int(pegar("AIPE_TIMEOUT_FETCH_S", "15")),
        tentativas_fetch=int(pegar("AIPE_TENTATIVAS_FETCH", "2")),
        maximo_trechos_por_termo=int(pegar("AIPE_MAX_TRECHOS_TERMO", "3")),
        limite_nota_minima=float(pegar("AIPE_NOTA_MINIMA", "40")),
        token_extensao=pegar("AIPE_TOKEN_EXTENSAO"),
        assinatura=pegar("AIPE_ASSINATURA"),
        token_telegram=pegar("AIPE_TELEGRAM_TOKEN"),
        openclaw_bin=pegar("AIPE_OPENCLAW_BIN"),
        llm_base_url=pegar("LLM_BASE_URL"),
        llm_api_key=pegar("LLM_API_KEY"),
        llm_modelo=pegar("LLM_MODELO"),
    )
    return config


_CHAVES_LLM = {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODELO"}


settings = carregar_config()
