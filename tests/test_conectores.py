"""Testes dos conectores: bloqueio anti-bot (busca web) e política de sites."""
import httpx
import pytest

from app.adapters import sites, websearch
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

    def fake_post(*args, **kwargs):
        return _Resposta(status_code=202, texto=pagina_anomalia)

    def fake_get(*args, **kwargs):
        return _Resposta(status_code=403, texto="captcha challenge")

    monkeypatch.setattr(websearch.httpx, "post", fake_post)
    monkeypatch.setattr(websearch.httpx, "get", fake_get)
    with pytest.raises(websearch.BuscaBloqueada):
        websearch.buscar("qualquer consulta")


def test_ddg_bloqueado_cai_para_brave(banco_tmp, monkeypatch):
    """NordVPN ativa: DDG bloqueia (202), Brave responde com resultados orgânicos."""
    pagina_anomalia = "<html>anomaly detection captcha</html>"
    pagina_brave = (
        '<html><main><section id="mixed-main">'
        '<div class="snippet" data-type="web"><a href="https://clinica.com.br/">r1</a></div>'
        '<div class="snippet" data-type="ad"><a href="https://anuncio.com/">ad</a></div>'
        '<div class="snippet" data-type="web"><a href="https://www.brave.com/interno">interno</a></div>'
        '<div class="snippet" data-type="web"><a href="https://outra.com.br/">r2</a></div>'
        "</section></main></html>"
    )

    def fake_post(*args, **kwargs):
        return _Resposta(status_code=202, texto=pagina_anomalia)

    def fake_get(*args, **kwargs):
        return _Resposta(status_code=200, texto=pagina_brave)

    monkeypatch.setattr(websearch.httpx, "post", fake_post)
    monkeypatch.setattr(websearch.httpx, "get", fake_get)
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

    def fake_post(*args, **kwargs):
        return _Resposta(status_code=200, texto=pagina)

    monkeypatch.setattr(websearch.httpx, "post", fake_post)
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
