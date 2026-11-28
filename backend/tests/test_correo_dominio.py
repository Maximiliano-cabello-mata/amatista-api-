"""Los enlaces de los correos usan AMATISTA_URL_PWA (dominio configurable)."""
from api import correo


def test_enlace_por_defecto_es_el_dominio_oficial(monkeypatch):
    """Comprueba que sin AMATISTA_URL_PWA el enlace del correo apunta a https://amatista-3dt.tech."""
    monkeypatch.delenv("AMATISTA_URL_PWA", raising=False)
    html = correo.construir_plantilla_html("a@b.cl", "123456", "correo")
    # Dentro del atributo href el & va escapado como &amp; (HTML válido; el cliente de correo lo decodifica).
    assert 'href="https://amatista-3dt.tech/#/confirmar?email=a%40b.cl&amp;codigo=123456"' in html


def test_enlace_sigue_a_amatista_url_pwa(monkeypatch):
    """Comprueba que el enlace del correo usa AMATISTA_URL_PWA (sin barra final)."""
    monkeypatch.setenv("AMATISTA_URL_PWA", "https://amatista.test/")
    assert correo.url_pwa_correo() == "https://amatista.test"
    html = correo.construir_plantilla_html("a@b.cl", "123456", "password")
    assert "https://amatista.test/#/recuperar" in html


def test_nombre_con_html_se_escapa_en_el_correo():
    """Comprueba que un nombre con etiquetas no inyecta HTML ni enlaces en el correo (saludo y frase de Goliat)."""
    malicioso = '<a href="https://phishing.example">Pulsa aquí</a><img src=x onerror=alert(1)>'
    html = correo.construir_plantilla_html("a@b.cl", "123456", "correo", malicioso)
    assert "<img" not in html
    assert '<a href="https://phishing' not in html
    assert "Hola, <strong>&lt;a href=&quot;https://phishing.example&quot;&gt;" in html
    assert "Bienvenido al taller, &lt;a href=" in html
