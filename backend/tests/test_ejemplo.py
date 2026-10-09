"""El ejemplo resuelto de cada práctica en la lección y en el enlace con Blender (motor 3.5)."""
from tests.test_addon import API
from tests.test_enlace import latir, vincular


def test_la_leccion_trae_el_ejemplo_resuelto(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    assert cliente.post(f"{API}/practicas/sincronizar?publicar=true", headers=admin).json()["errores"] == []
    _, alumno = crear_cuenta()
    datos = cliente.get(f"{API}/practicas/blender.bpi.m3.pelota", headers=alumno).json()
    ejemplo = datos["ejemplo"]
    assert ejemplo["titulo"] == "Pelota que rebota"
    assert ejemplo["revisa"] == ["Animación", "Archivo"]
    assert any("fotograma 12" in paso for paso in ejemplo["pasos"])
    assert ejemplo["codigo"][0] == {"esfera": {"nombre": "Pelota", "loc": [0, 0, 4]}}
    # La ruta termina con el paso que compara la práctica con el ejemplo (el servidor lo evalúa con ese id).
    assert datos["pasos"][-1] == {"id": "ejemplo", "titulo": "Tu práctica coincide con el ejemplo"}
    lista = cliente.get(f"{API}/practicas", headers=alumno).json()
    pelota = next(p for p in lista["practicas"] if p["id"] == "blender.bpi.m3.pelota")
    assert pelota["pasos"][-1]["id"] == "ejemplo"


def test_ver_el_ejemplo_desde_la_plataforma(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender, practica_id="blender.bp.m2.espada")
    for tipo in ("ver_ejemplo", "volver_practica"):
        r = cliente.post(f"{API}/ordenes", json={"tipo": tipo, "practica_id": "blender.bp.m2.espada"}, headers=alumno)
        assert r.status_code == 200 and r.json()["entregada"], r.text
        orden = latir(cliente, blender, practica_id="blender.bp.m2.espada")["orden"]
        assert orden["tipo"] == tipo
        latir(cliente, blender, practica_id="blender.bp.m2.espada", orden_hecha=orden["id"])


def test_la_lista_llega_agrupada_por_aspecto(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    detalle = {"titulo": "Tu práctica coincide con el ejemplo", "modo": "EJEMPLO", "lista": [
        {"texto": "Rueda", "ok": True, "estado": "Bien", "aspecto": "La figura"},
        {"texto": "«Cromo»: metálico", "ok": False, "estado": "Falta", "consejo": "Sube Metálico a 1.",
         "aspecto": "Materiales"}]}
    latir(cliente, blender, practica_id="blender.bp.m1.tren", detalle=detalle)
    (b,) = cliente.get(f"{API}/enlace", headers=alumno).json()["blender"]
    assert [i["aspecto"] for i in b["detalle"]["lista"]] == ["La figura", "Materiales"]
    assert b["detalle"]["modo"] == "EJEMPLO"
