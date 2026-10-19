"""Testes de extração de texto, evidências e integridade."""
from app.engine.evidence import coletar_evidencias, extrair_pagina, hash_conteudo, html_para_texto
from tests.conftest import HTML_EXEMPLO


def test_html_para_texto_remove_tags_e_scripts():
    html = "<html><script>var x=1;</script><style>p{}</style><body><h1>Titulo</h1><p>Texto &amp; mais</p></body></html>"
    texto = html_para_texto(html)
    assert "var x=1" not in texto
    assert "p{}" not in texto
    assert "Titulo" in texto
    assert "Texto & mais" in texto


def test_extrair_pagina_meta_e_identificadores():
    pagina = extrair_pagina("https://exemplo.com.br/", HTML_EXEMPLO)
    assert pagina.titulo == "Clinica Exemplo — Fisioterapia em Campinas"
    assert "fisioterapia" in pagina.meta_description.lower()
    assert pagina.cnpjs == ["19.131.243/0001-97"]
    assert pagina.ceps == ["13010-000"]
    assert any("(19) 3232-1010" in t for t in pagina.telefones)


def test_evidencias_associam_criterio_url_e_data():
    pagina = extrair_pagina("https://exemplo.com.br/", HTML_EXEMPLO)
    criterios = [
        {"id": "c1", "tipo": "segmento", "termo": "fisioterapia", "peso": 3.0},
        {"id": "c2", "tipo": "palavra_chave", "termo": "convênios", "peso": 1.5},
    ]
    evidencias = coletar_evidencias(pagina, criterios, "https://exemplo.com.br/", "2026-10-19T12:00:00Z")
    por_criterio = {e["criterio_id"] for e in evidencias}
    assert {"c1", "c2"} <= por_criterio
    for e in evidencias:
        assert e["url"] == "https://exemplo.com.br/"
        assert e["data_coleta"] == "2026-10-19T12:00:00Z"
        # trecho é o texto ORIGINAL da página, sem parafrasear
        assert any(palavra in HTML_EXEMPLO for palavra in e["trecho"].split()[:3])


def test_evidencias_sem_duplicatas_sobrepostas():
    pagina = extrair_pagina("https://exemplo.com.br/", HTML_EXEMPLO)
    criterios = [{"id": "c1", "tipo": "segmento", "termo": "fisioterapia", "peso": 3.0}]
    evidencias = coletar_evidencias(pagina, criterios, "https://exemplo.com.br/", "2026-10-19T12:00:00Z")
    trechos = [e["trecho"] for e in evidencias]
    assert len(trechos) == len(set(trechos))  # janelas sobrepostas são descartadas


def test_evidencias_campo_titulo():
    pagina = extrair_pagina("https://exemplo.com.br/", HTML_EXEMPLO)
    criterios = [{"id": "c1", "tipo": "segmento", "termo": "fisioterapia", "peso": 3.0}]
    evidencias = coletar_evidencias(pagina, criterios, "https://exemplo.com.br/", "2026-10-19T12:00:00Z")
    campos = {e["campo"] for e in evidencias}
    assert "titulo" in campos  # termo aparece no <title>


def test_hash_conteudo_estavel():
    assert hash_conteudo("abc") == hash_conteudo("abc")
    assert hash_conteudo("abc") != hash_conteudo("abd")
    assert len(hash_conteudo("abc")) == 64
