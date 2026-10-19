"""Testes da pontuação explicável (fato corroborado / parcial / inferido / ausente)."""
from app.engine.scoring import pontuar

CRITERIOS = [
    {"id": "c1", "tipo": "segmento", "termo": "fisioterapia", "peso": 3.0},
    {"id": "c2", "tipo": "localizacao", "termo": "Campinas (SP)", "peso": 2.0},
    {"id": "c3", "tipo": "palavra_chave", "termo": "convênios", "peso": 1.5},
]
FONTE_OSM = "https://www.openstreetmap.org/node/1"
FONTE_SITE = "https://clinica.com.br/"


def _evidencia(cid, url):
    return {"criterio_id": cid, "termo": "x", "trecho": "t", "url": url, "data_coleta": "2026-10-19T12:00:00Z", "campo": "texto"}


def test_atendido_com_corroboracao_independente():
    evidencias = [_evidencia("c1", FONTE_OSM), _evidencia("c1", FONTE_SITE)]
    resultado = pontuar(CRITERIOS, evidencias, url_fonte_inicial=FONTE_OSM)
    c1 = resultado["por_criterio"][0]
    assert c1["status"] == "atendido"


def test_parcial_com_apenas_fonte_de_descoberta():
    evidencias = [_evidencia("c1", FONTE_OSM)]
    resultado = pontuar(CRITERIOS, evidencias, url_fonte_inicial=FONTE_OSM)
    c1 = resultado["por_criterio"][0]
    assert c1["status"] == "parcial"
    # pesos: c1 parcial 3*0.7 = 2.1 de total 6.5
    assert resultado["pontuacao"] == round(100 * 2.1 / 6.5, 1)


def test_inferido_e_ausente():
    sinais = [{"criterio_id": "c2", "detalhe": "domínio .br sugere Brasil", "fonte": FONTE_SITE}]
    resultado = pontuar(CRITERIOS, [], sinais, url_fonte_inicial=FONTE_OSM)
    status = {c["criterio_id"]: c["status"] for c in resultado["por_criterio"]}
    assert status["c2"] == "inferido"
    assert status["c1"] == "ausente"
    assert status["c3"] == "ausente"
    # inferência vale metade do peso (2.0*0.5=1.0 de 6.5)
    assert resultado["pontuacao"] == round(100 * 1.0 / 6.5, 1)


def test_explicacao_inclui_natureza_das_evidencias():
    evidencias = [_evidencia("c1", FONTE_SITE)]
    resultado = pontuar(CRITERIOS, evidencias, url_fonte_inicial=FONTE_OSM)
    c1 = resultado["por_criterio"][0]
    assert c1["evidencias"][0]["natureza"] == "fato"
    assert c1["inferencias"] == []
