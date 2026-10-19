"""Testes do interpretador de ICP (determinístico)."""
from app.engine.icp import consultas_pesquisa, extrair_criterios


def test_extrai_segmento_localizacao_e_palavras_chave():
    resultado = extrair_criterios(
        "Procuro clínicas de fisioterapia em Campinas (SP), com site próprio, "
        "que atendam convênios e queiram aumentar a agenda."
    )
    tipos = {c["tipo"] for c in resultado["criterios"]}
    assert "segmento" in tipos
    assert "localizacao" in tipos
    assert "palavra_chave" in tipos
    segmento = next(c for c in resultado["criterios"] if c["tipo"] == "segmento")
    assert segmento["consulta"] == "fisioterapia"
    local = next(c for c in resultado["criterios"] if c["tipo"] == "localizacao")
    assert "Campinas" in local["termo"]
    assert resultado["pais"] == "BR"
    assert resultado["alertas"] == []


def test_extrai_porte():
    resultado = extrair_criterios("Escritórios de contabilidade em Porto Alegre com mais de 50 funcionários.")
    porte = [c for c in resultado["criterios"] if c["tipo"] == "porte"]
    assert porte and "50" in porte[0]["termo"]


def test_sem_localizacao_gera_alerta():
    resultado = extrair_criterios("Academias com pilates que usem software de agendamento.")
    assert resultado["pais"] is None
    assert any("localização" in a.lower() for a in resultado["alertas"])
    tipos = {c["tipo"] for c in resultado["criterios"]}
    assert "segmento" in tipos


def test_consultas_combinam_segmento_e_local():
    resultado = extrair_criterios("Dentistas em Curitiba.")
    consultas = consultas_pesquisa(resultado["criterios"])
    assert consultas == ["dentista Curitiba"]


def test_pesos_padrao_por_tipo():
    resultado = extrair_criterios("Advocacia em Santos.")
    pesos = {c["tipo"]: c["peso"] for c in resultado["criterios"]}
    assert pesos["segmento"] == 3.0
    assert pesos["localizacao"] == 2.0
