"""Testes dos conectores: bloqueio anti-bot (busca web) e política de sites."""
import httpx
import pytest

import pytest

from app.adapters import sites, websearch


@pytest.fixture(autouse=True)
def _sem_cooldown():
    websearch.reset_cooldown()
    websearch._cache.clear()
    yield
    websearch.reset_cooldown()
    websearch._cache.clear()
from app.engine.research import dominio_corresponde_ao_nome, dominio_de


class _Resposta:
    def __init__(self, status_code=200, texto="", headers=None, url="https://x/"):
        self.status_code = status_code
        self.text = texto
        self.headers = headers or {}
        self.url = url

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erro", request=None, response=None)


def test_busca_web_todos_os_motores_bloqueados(banco_tmp, monkeypatch):
    # fixture REAL: página de anomalia capturada neste ambiente (HTTP 202, DDG)
    pagina_anomalia = (
        "<html><body>If this error persists, <a href='/'>anomaly</a> detection "
        "was triggered. Please solve a captcha to continue.</body></html>"
    )

    def fake_curl(metodo, url, dados=None, timeout_s=None):
        if metodo == "POST":
            return 202, pagina_anomalia
        return 403, "captcha challenge"

    monkeypatch.setattr(websearch, "_curl", fake_curl)
    with pytest.raises(websearch.BuscaBloqueada):
        websearch.buscar("qualquer consulta")


def test_todas_as_fontes_bloqueadas(banco_tmp, monkeypatch):
    def fake_curl(metodo, url, dados=None, timeout_s=None):
        return 403, "captcha challenge"

    monkeypatch.setattr(websearch, "_curl", fake_curl)
    with pytest.raises(websearch.BuscaBloqueada):
        websearch.buscar_detalhado("qualquer consulta", limite=5)


def test_ddg_bloqueado_cai_para_brave(banco_tmp, monkeypatch):
    """NordVPN ativa: DDG bloqueia (202), Brave responde com resultados orgânicos."""
    pagina_anomalia = "<html><title>Captcha</title><body>anomaly detection</body></html>"
    pagina_brave = (
        '<html><main><section id="mixed-main">'
        '<div class="snippet" data-type="web"><a href="https://clinica.com.br/">r1</a></div>'
        '<div class="snippet" data-type="ad"><a href="https://anuncio.com/">ad</a></div>'
        '<div class="snippet" data-type="web"><a href="https://www.brave.com/interno">interno</a></div>'
        '<div class="snippet" data-type="web"><a href="https://outra.com.br/">r2</a></div>'
        "</section></main></html>"
    )

    def fake_curl(metodo, url, dados=None, timeout_s=None):
        if "duckduckgo" in url:
            return 202, pagina_anomalia
        return 200, pagina_brave

    monkeypatch.setattr(websearch, "_curl", fake_curl)
    urls = websearch.buscar("consulta", limite=5)
    assert urls == ["https://clinica.com.br/", "https://outra.com.br/"]


def test_busca_web_extrai_links_organicos_e_descarta_anuncios(banco_tmp, monkeypatch):
    pagina = (
        '<html>'
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fclinica.com.br%2F&amp;rut=abc">r1</a>'
        '<a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=loja.com&amp;ad_type=txad">anuncio</a>'
        '<a class="result__a" href="https://direta.com.br/">r2</a>'
        '</html>'
    )

    monkeypatch.setattr(websearch, "_curl", lambda metodo, url, dados=None, timeout_s=None: (200, pagina))
    urls = websearch.buscar("consulta", limite=5)
    assert "https://clinica.com.br/" in urls
    assert "https://direta.com.br/" in urls
    assert all("y.js" not in u and "ad_domain" not in u for u in urls)


class _ClienteFalso:
    def __init__(self, resposta):
        self._resposta = resposta

    def get(self, url):
        return self._resposta

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_site_respeita_robots_txt(banco_tmp, monkeypatch):
    robots = "User-agent: *\nDisallow: /\n"
    respostas = {"robots": _Resposta(200, robots), "pagina": _Resposta(200, "<html>ok</html>")}
    pedidos = []

    def cliente_falso():
        pedidos.append(1)
        resposta = respostas["robots"] if len(pedidos) == 1 else respostas["pagina"]
        return _ClienteFalso(resposta)

    monkeypatch.setattr(sites, "_cliente", cliente_falso)
    resultado = sites.coletar_site("https://bloqueado.com.br/")
    assert resultado.estado == "robots"
    assert len(pedidos) == 1  # página nunca foi acessada


def test_site_detecta_captcha_e_levanta_bloqueio(banco_tmp, monkeypatch):
    robots = _Resposta(200, "User-agent: *\nAllow: /\n")
    pagina_captcha = _Resposta(
        403, "<html>Just a moment... Cloudflare challenge</html>", headers={"server": "cloudflare"}
    )
    estado = {"robots": False}

    def _cliente():
        if not estado["robots"]:
            estado["robots"] = True
            return _ClienteFalso(robots)
        return _ClienteFalso(pagina_captcha)

    monkeypatch.setattr(sites, "_cliente", _cliente)
    with pytest.raises(sites.BloqueioDetectado):
        sites.coletar_site("https://protegido.com.br/")


def test_site_html_normal_e_cap_de_tamanho(banco_tmp, monkeypatch):
    def _cliente():
        chamadas = {"n": 0}

        class C:
            def __init__(self):
                self.chamadas = chamadas

            def get(self, url):
                chamadas["n"] += 1
                if url.endswith("robots.txt"):
                    return _Resposta(404, "not found")
                return _Resposta(200, "<html><body>conteudo</body></html>", headers={"content-type": "text/html"}, url=url)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return C()

    monkeypatch.setattr(sites, "_cliente", _cliente)
    resultado = sites.coletar_site("https://ok.com.br/")
    assert resultado.estado == "ok"
    assert "conteudo" in resultado.html


# ---------- heurística de site oficial ----------

def test_dominio_corresponde_ao_nome():
    assert dominio_corresponde_ao_nome("alexandreerix.com.br", "Dr. Alexandre Érix - Fisioterapia")
    assert not dominio_corresponde_ao_nome("totalpass.com", "Studio Dourado Pilates & Fisioterapia")
    assert not dominio_corresponde_ao_nome(None, "Qualquer")


def test_dominio_de_normaliza_www():
    assert dominio_de("https://www.clinica.com.br/contato") == "clinica.com.br"
    assert dominio_de("nao e url") in (None, "")


def test_fontes_rss_e_wikipedia_na_cadeia(banco_tmp, monkeypatch):
    """Com Brave/DDG bloqueados, o Bing RSS responde na cadeia expandida."""
    websearch.reset_cooldown()

    def fake_curl(metodo, url, dados=None, timeout_s=None):
        if "duckduckgo" in url:
            return 202, "<html>anomaly captcha</html>"  # DDG bloqueado (fixture real)
        if "brave" in url:
            return 403, "captcha challenge"  # Brave bloqueado
        if "bing.com" in url:
            return 200, ("<rss><item><title>OLX Carros</title>"
                         "<link>https://www.olx.com.br/carros</link></item></rss>")
        return 403, "captcha challenge"  # demais fontes bloqueadas

    monkeypatch.setattr(websearch, "_curl", fake_curl)
    urls = [r["url"] for r in websearch.buscar_detalhado("carros de luxo sjc", limite=5)]
    assert "https://www.olx.com.br/carros" in urls
    websearch.reset_cooldown()


def test_wikipedia_na_cadeia(banco_tmp, monkeypatch):
    websearch.reset_cooldown()
    json_wiki = '{"query": {"search": [{"title": "Cardiologia"}, {"title": "Roberto Kalil Filho"}]}}'

    def fake_curl(metodo, url, dados=None, timeout_s=None):
        if "wikipedia" in url:
            return 200, json_wiki
        return 403, "captcha challenge"

    monkeypatch.setattr(websearch, "_curl", fake_curl)
    rs = websearch.buscar_detalhado("cardiologia", limite=3)
    assert any("Cardiologia" in r["titulo"] for r in rs)
