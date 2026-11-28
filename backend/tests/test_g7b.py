"""Contrato G7B consumido por Amatista: aislado de Oracle y de la red real."""
import pytest

from api.datos import g7b


def _publicacion():
    """Publicación G7B válida de ejemplo (contrato g7b.geoda.estado/1 con conteos agregados)."""
    return {
        "contrato": "g7b.geoda.estado/1", "version": "v1",
        "cubo": {"version": "v1", "mediciones": 3003, "aceptadas": 3,
                 "revision": 3000, "recomendaciones_habilitadas": False},
        "conocimiento": {"documentos": 992, "origen": "mongo_conocimiento_3d"},
        "perfiles": [],
    }


def test_estado_consulta_y_cachea_publicacion_agrupada(cliente, monkeypatch):
    """Comprueba que GET /api/g7b/v1/estado descarga la publicación una vez y después responde desde la caché."""
    llamadas = []
    g7b._reiniciar_cache()
    monkeypatch.setattr(g7b, "_descargar", lambda: llamadas.append(1) or g7b.validar(_publicacion()))
    resultado = cliente.get("/api/g7b/v1/estado")
    assert resultado.status_code == 200
    assert resultado.json()["estado"] == "conectado"
    assert resultado.json()["cubo"]["aceptadas"] == 3
    assert resultado.json()["conocimiento"]["documentos"] == 992
    assert "origen" not in resultado.json()["conocimiento"]
    assert cliente.get("/api/g7b/v1/estado").status_code == 200
    assert len(llamadas) == 1
    g7b._reiniciar_cache()


def test_g7b_fuera_de_linea_no_bloquea_a_amatista(cliente, monkeypatch):
    """Comprueba que con G7B caído la ruta responde 200 con estado «sin_conexion»."""
    g7b._reiniciar_cache()
    def caida():
        """Simula la descarga fallida por falta de red."""
        raise OSError("sin red")
    monkeypatch.setattr(g7b, "_descargar", caida)
    resultado = cliente.get("/api/g7b/v1/estado")
    assert resultado.status_code == 200
    assert resultado.json()["estado"] == "sin_conexion"
    assert resultado.json()["perfiles"] == []
    g7b._reiniciar_cache()


def test_ultima_copia_se_conserva_si_cae_g7b(cliente, monkeypatch):
    """Comprueba que si G7B cae después de una descarga se sirve la copia anterior con estado «cache»."""
    g7b._reiniciar_cache()
    monkeypatch.setattr(g7b, "_descargar", lambda: g7b.validar(_publicacion()))
    assert cliente.get("/api/g7b/v1/estado").json()["estado"] == "conectado"
    monkeypatch.setattr(g7b, "_vence", 0.0)
    def caida():
        """Simula la descarga fallida por falta de red."""
        raise OSError("sin red")
    monkeypatch.setattr(g7b, "_descargar", caida)
    resultado = cliente.get("/api/g7b/v1/estado").json()
    assert resultado["estado"] == "cache"
    assert resultado["cubo"]["mediciones"] == 3003
    g7b._reiniciar_cache()


@pytest.mark.parametrize("cambio", [
    {"perfiles": [{"nivel": "N2", "estado": "confiable"}]},
    {"version": "equivocado"},
    {"cubo": {"version": "v1", "mediciones": 2, "aceptadas": 3,
              "revision": 3000, "recomendaciones_habilitadas": False}},
])
def test_no_acepta_recomendaciones_no_certificadas(cambio):
    """Comprueba que validar rechaza publicaciones con recomendaciones habilitadas o perfiles."""
    with pytest.raises(ValueError):
        g7b.validar({**_publicacion(), **cambio})


def test_no_acepta_valores_booleanos_como_conteos():
    """Comprueba que validar no acepta true como conteo de documentos."""
    p = _publicacion()
    p["conocimiento"] = {"documentos": True}
    with pytest.raises(ValueError):
        g7b.validar(p)
