"""Protección del add-on en el equipo del alumno (docs/seguridad/02_proteccion_del_codigo.md).

- Cada paquete trae integridad.json (SHA-256 de cada archivo).
- Cada descarga con cuenta trae licencia.json firmada (marca de agua).
- Los intentos de una copia no verificada quedan marcados, o se rechazan
  con AMATISTA_ADDON_VERIFICADO=exigir.
"""
import hashlib
import io
import json
import zipfile

from contenido import motor
from herramientas import verificar_licencia

API = "/api/addon/v1"
CARPETA = motor.construir.CARPETA_PAQUETE


def extension_de(paquete: bytes) -> zipfile.ZipFile:
    exterior = zipfile.ZipFile(io.BytesIO(paquete))
    interior = exterior.getinfo(f"{CARPETA}/amatista-{motor.VERSION_ADDON}.zip")
    # La extensión va guardada tal cual (ya viene comprimida).
    assert interior.compress_type == zipfile.ZIP_STORED
    return zipfile.ZipFile(io.BytesIO(exterior.read(interior)))


def test_el_paquete_trae_la_integridad_de_cada_archivo():
    extension = zipfile.ZipFile(io.BytesIO(motor.construir.construir_extension()))
    integridad = json.loads(extension.read("integridad.json"))
    variables = {"config.json", "integridad.json", "licencia.json"}
    nombres = [n for n in extension.namelist() if n not in variables]
    assert sorted(integridad["archivos"]) == sorted(nombres)
    for nombre in nombres:
        assert integridad["archivos"][nombre] == hashlib.sha256(extension.read(nombre)).hexdigest()
    assert integridad["huella"] == motor.construir.huella_oficial()


def test_el_mismo_codigo_da_el_mismo_paquete():
    """Agregar config.json a la base en caché da el mismo .zip que construirlo entero."""
    a = motor.construir.construir_extension(servidor="https://a", vinculo={"id": "1", "secreto": "s"})
    b = motor.construir.construir_extension(servidor="https://a", vinculo={"id": "1", "secreto": "s"})
    assert a == b


def test_descarga_con_cuenta_trae_marca_de_agua_firmada(cliente, crear_cuenta, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AMATISTA_SECRETO_FIRMA", "secreto-de-prueba")
    alumno_id, alumno = crear_cuenta()
    personal = cliente.get(f"{API}/descargas/windows", headers=alumno)
    licencia = json.loads(extension_de(personal.content).read("licencia.json"))
    assert licencia["cuenta"] == alumno_id and licencia["firma"]
    from api.addon import licencia_valida

    assert licencia_valida(licencia)
    assert not licencia_valida({**licencia, "cuenta": "otra"})  # no se puede cambiar de dueño

    # La herramienta del servidor dice de quién salió una copia.
    ruta = tmp_path / "copia.zip"
    ruta.write_bytes(personal.content)
    verificar_licencia.main([str(ruta)])
    salida = capsys.readouterr().out
    assert alumno_id in salida and "FIRMA VÁLIDA" in salida and "todos son los originales" in salida

    # La descarga pública no lleva marca de agua de nadie.
    publica = extension_de(cliente.get(f"{API}/descargas/windows").content)
    assert "licencia.json" not in publica.namelist()


def test_intento_de_copia_no_verificada(cliente, crear_cuenta, monkeypatch):
    from database import conexion
    from database.modelos import EventoAprendizaje
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    _, admin = crear_cuenta(rol="admin")
    _, alumno = crear_cuenta()
    cliente.post(f"{API}/practicas/sincronizar?publicar=true", headers=admin)
    practica = "blender.bp.m1.explora"
    cuerpo = {"practica_id": practica, "escena": {"objetos": []}}
    oficial = {**alumno, "X-Amatista-Integridad": "oficial", "X-Amatista-Huella": motor.construir.huella_oficial()}

    primera = cliente.post(f"{API}/intentos", json=cuerpo, headers=oficial)
    assert primera.json()["copia_verificada"] is True
    modificada = {**alumno, "X-Amatista-Integridad": "modificada", "X-Amatista-Huella": "0" * 64}
    respuesta = cliente.post(f"{API}/intentos", json=cuerpo, headers=modificada)
    assert respuesta.status_code == 200 and respuesta.json()["copia_verificada"] is False
    with Session(conexion.motor()) as db:
        datos = [e.datos for e in db.scalars(select(EventoAprendizaje).where(EventoAprendizaje.tipo == "activity_submitted"))]
    assert any('"ok":0' in (d or "") for d in datos) and any('"ok":1' in (d or "") for d in datos)

    # Con la política «exigir» una copia que miente sobre su huella no califica.
    monkeypatch.setenv("AMATISTA_ADDON_VERIFICADO", "exigir")
    mentirosa = {**alumno, "X-Amatista-Integridad": "oficial", "X-Amatista-Huella": "f" * 64}
    assert cliente.post(f"{API}/intentos", json=cuerpo, headers=mentirosa).status_code == 403
    assert cliente.post(f"{API}/intentos", json=cuerpo, headers=oficial).status_code == 200
