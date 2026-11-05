import pytest
from ai_tutor.motor_ia import MotorIALocal, normalizar_texto


def test_normalizar_texto():
    assert normalizar_texto("¿Cómo EXTRUYO una cara?") == ["como", "extruyo", "una", "cara"]
    assert normalizar_texto("Bisel con Ctrl+B!") == ["bisel", "con", "ctrl+b"]


def test_carga_conocimiento_y_fichas():
    motor = MotorIALocal()
    assert len(motor.fichas) >= 20
    assert len(motor.zonas) >= 5


def test_busqueda_semantica_fichas():
    motor = MotorIALocal()

    # Pregunta sobre extruir
    ficha_extruir = motor.buscar_ficha_relevante("¿Cómo extruyo una cara con la tecla E?")
    assert ficha_extruir is not None
    assert ficha_extruir["id"] == "mesh.extrude"

    # Pregunta sobre corte de bucle
    ficha_corte = motor.buscar_ficha_relevante("Quiero hacer un corte de bucle con Ctrl+R")
    assert ficha_corte is not None
    assert ficha_corte["id"] == "mesh.loopcut"

    # Pregunta sobre bisel
    ficha_bisel = motor.buscar_ficha_relevante("El bisel sale chueco por la escala")
    assert ficha_bisel is not None
    assert ficha_bisel["id"] == "modifier.bevel"


def test_consulta_rag_directa_sin_llm():
    motor = MotorIALocal(url_llm=None)
    respuesta = motor.consultar("¿Cómo extruyo en Blender?")
    assert respuesta["origen"] == "ia-rag-local"
    assert respuesta["ficha"] == "mesh.extrude"
    assert "Extruir" in respuesta["texto"]
    assert "error_tipico" in respuesta


def test_estado_salud():
    motor = MotorIALocal()
    salud = motor.estado_salud()
    assert salud["estado"] == "ok"
    assert salud["fichas_cargadas"] >= 20
