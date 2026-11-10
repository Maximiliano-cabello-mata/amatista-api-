"""Las API de Amatista: qué responsabilidad tiene cada una y qué rutas le pertenecen.

Corren en un solo proceso (comparten la conexión a la base y los límites por
IP), pero cada una vive en su paquete, tiene sus prefijos y firma sus errores
con su nombre (api/errores.py). Una ruta nueva se agrega al router de su API;
una API nueva se agrega a APIS.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Tuple

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class Api:
    nombre: str  # identificador estable: aparece en los códigos de error
    titulo: str
    responsabilidad: str
    prefijos: Tuple[str, ...]
    routers: Callable[[], Tuple[APIRouter, ...]]
    activa: Callable[[], bool] = lambda: True


def _plataforma():
    from api.plataforma import auth, blender, buzon, contenido, eventos, niveles, perfil, progreso, sesiones, sincronizar

    # sesiones: /api/iniciar-sesion heredado (alumnos anónimos). sincronizar: progreso
    # y eventos en una petición comprimida. niveles: mapa del curso (v3).
    return (auth.router, sesiones.router, progreso.router, eventos.router, sincronizar.router,
            contenido.router, niveles.router, blender.router, perfil.router, buzon.router)


def _motor():
    from api.motor import addon, enlace, taller

    # addon: complemento y motor de prácticas (sql/007). enlace: plataforma ↔ Blender
    # en vivo (sql/010). taller: el modelo del alumno por habilidad (sql/012).
    return (addon.router, enlace.router, taller.router)


def _aula():
    from api.aula import evaluacion

    return (evaluacion.router,)  # panel del profesor y «Mi clase» (sql/014)


def _administracion():
    from api.administracion import admin

    return (admin.router,)


def _datos():
    from api.datos import g7b, nube

    # g7b: estado agregado G7B -> Geoda (sin Oracle). nube: propuesta Amatista
    # Cloud, solo con AMATISTA_PROPUESTA_NUBE=1.
    return (g7b.router,) + ((nube.router,) if os.getenv("AMATISTA_PROPUESTA_NUBE") == "1" else ())


APIS: Tuple[Api, ...] = (
    Api("plataforma", "API de la plataforma",
        "Lo que usa la web del alumno: cuentas, progreso, sincronización, contenido, perfil y buzón.",
        ("/api/auth", "/api/contenido", "/api/blender", "/api/perfil", "/api/buzon", "/api"), _plataforma),
    Api("motor", "API del motor",
        "Lo que usa el complemento de Blender: vinculación, prácticas, intentos, enlace en vivo y taller.",
        ("/api/addon/v1",), _motor),
    Api("aula", "API del aula",
        "Panel del profesor y «Mi clase»: grupos, prácticas asignadas, entregas y evaluación.",
        ("/api/evaluacion",), _aula),
    Api("administracion", "API de administración",
        "Usuarios, contenido, sistema y auditoría. Solo profesores y administradores.",
        ("/api/admin",), _administracion),
    Api("datos", "API de datos",
        "Estado agregado de G7B para Geoda y la propuesta de Blender en la nube.",
        ("/api/g7b/v1", "/api/nube/v1"), _datos),
)

# El prefijo más largo gana: /api/addon/v1/... es del motor aunque /api sea de la plataforma.
_POR_PREFIJO = sorted(((p, a.nombre) for a in APIS for p in a.prefijos), key=lambda par: -len(par[0]))
SISTEMA = "sistema"  # /, /api/salud, /api/apis y lo que no es de ninguna API


def api_de(ruta: str) -> str:
    """Nombre de la API a la que pertenece una ruta."""
    if ruta in ("/api/salud", "/api/apis"):
        return SISTEMA
    for prefijo, nombre in _POR_PREFIJO:
        if ruta == prefijo or ruta.startswith(prefijo + "/"):
            return nombre
    return SISTEMA


def registrar(app: FastAPI) -> None:
    """Agrega a la aplicación los routers de cada API, en orden."""
    for api in APIS:
        for router in api.routers():
            app.include_router(router)


def resumen() -> list:
    """Cada API con sus prefijos y cuántas operaciones publica (para GET /api/apis)."""
    datos = []
    for api in APIS:
        operaciones = sum(
            len(set(getattr(ruta, "methods", None) or ()) - {"HEAD", "OPTIONS"})
            for router in api.routers() for ruta in router.routes
        )
        datos.append({"api": api.nombre, "titulo": api.titulo, "responsabilidad": api.responsabilidad,
                      "prefijos": list(api.prefijos), "operaciones": operaciones})
    return datos
