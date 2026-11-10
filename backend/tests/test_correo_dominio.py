"""Los enlaces de los correos usan AMATISTA_URL_PWA (dominio configurable)."""
from api import correo


def test_enlace_por_defecto_es_el_dominio_oficial(monkeypatch):
    monkeypatch.delenv("AMATISTA_URL_PWA", raising=False)
    html = correo.construir_plantilla_html("a@b.cl", "123456", "correo")
    assert "https://amatista-3dt.tech/#/confirmar?email=a%40b.cl&codigo=123456" in html


def test_enlace_sigue_a_amatista_url_pwa(monkeypatch):
    monkeypatch.setenv("AMATISTA_URL_PWA", "https://amatista.test/")
    assert correo.url_pwa_correo() == "https://amatista.test"
    html = correo.construir_plantilla_html("a@b.cl", "123456", "password")
    assert "https://amatista.test/#/recuperar" in html
