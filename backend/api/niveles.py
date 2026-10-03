"""Niveles de los cursos y mapa curso > nivel > módulo > lección (reestructuración v3).

- GET  /api/contenido/niveles?curso_id=blender (profesor o admin)
- POST /api/contenido/niveles (admin): crea un nivel en borrador.
- PUT  /api/contenido/niveles/{nivel_id} (admin): textos y estado.
- POST /api/contenido/niveles/sembrar (admin): crea los cinco niveles de
  Blender de la propuesta (contenido.plantillas.NIVELES_BLENDER) que falten.
- GET  /api/contenido/mapa/{curso_id} (profesor o admin): el mapa completo
  con lo que le falta a la ficha de cada lección (lo mismo que la vista
  V_AMATISTA_MAPA de sql/006, sin depender de Oracle).

El catálogo público (api/contenido.py) solo muestra los niveles publicados.
Asignar un módulo a un nivel: PUT /api/contenido/modulos/{id} {"nivel_id": ...}.
"""
from collections import defaultdict
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.contenido import confirmar, leer_json, nivel_meta, obtener_curso
from api.dependencias import requiere_rol
from contenido.plantillas import NIVELES_BLENDER
from contenido.validacion import MAX_ID, PATRON_ID, pendientes_ficha
from database.conexion import obtener_db
from database.modelos import Curso, Leccion, Modulo, Nivel, Usuario, ahora

router = APIRouter(prefix="/api/contenido", tags=["niveles"])

lector = requiere_rol("profesor", "admin")
editor = requiere_rol("admin")

Estado = Literal["borrador", "publicado", "archivado"]
PATRON_RAMA = r"^[a-z0-9][a-z0-9_-]*$"


def id_nivel(curso_id: str, numero: int, rama: Optional[str]) -> str:
    """blender + 1 → blender-n1; blender + 5 + web → blender-n5-web."""
    return f"{curso_id}-n{numero}" + (f"-{rama}" if rama else "")


def obtener_nivel(db: Session, nivel_id: str) -> Nivel:
    nivel = db.get(Nivel, nivel_id)
    if nivel is None:
        raise HTTPException(status_code=404, detail=f"No existe el nivel «{nivel_id}».")
    return nivel


class NivelNuevo(BaseModel):
    curso_id: str = Field(min_length=1, max_length=MAX_ID)
    numero: int = Field(ge=1, le=5)
    rama: Optional[str] = Field(default=None, min_length=1, max_length=30, pattern=PATRON_RAMA)
    titulo: str = Field(min_length=1, max_length=100)
    perfil: Optional[str] = Field(default=None, max_length=500)
    proyecto: Optional[str] = Field(default=None, max_length=300)
    criterio_salida: Optional[str] = Field(default=None, max_length=500)


class CambiosNivel(BaseModel):
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=100)
    perfil: Optional[str] = Field(default=None, max_length=500)
    proyecto: Optional[str] = Field(default=None, max_length=300)
    criterio_salida: Optional[str] = Field(default=None, max_length=500)
    estado: Optional[Estado] = None


@router.get("/niveles")
def listar_niveles(curso_id: Optional[str] = None, db: Session = Depends(obtener_db), _: Usuario = Depends(lector)):
    consulta = select(Nivel).order_by(Nivel.curso_id, Nivel.numero, Nivel.rama, Nivel.id)
    if curso_id:
        consulta = consulta.where(Nivel.curso_id == curso_id)
    return {"niveles": [nivel_meta(n) for n in db.scalars(consulta)]}


@router.post("/niveles", status_code=201)
def crear_nivel(cuerpo: NivelNuevo, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    curso = obtener_curso(db, cuerpo.curso_id)
    if cuerpo.rama and cuerpo.numero != 5:
        raise HTTPException(status_code=422, detail="Solo el nivel 5 tiene ramas (web, animacion, producto...).")
    nivel_id = id_nivel(curso.id, cuerpo.numero, cuerpo.rama)
    if len(nivel_id) > MAX_ID or not PATRON_ID.match(nivel_id):
        raise HTTPException(status_code=422, detail="El id del nivel quedaría demasiado largo: usa una rama más corta.")
    repetido = db.scalars(
        select(Nivel.id).where(Nivel.curso_id == curso.id, Nivel.numero == cuerpo.numero, Nivel.rama == cuerpo.rama)
        if cuerpo.rama
        else select(Nivel.id).where(Nivel.curso_id == curso.id, Nivel.numero == cuerpo.numero, Nivel.rama.is_(None))
    ).first()
    if repetido or db.get(Nivel, nivel_id) is not None:
        raise HTTPException(status_code=409, detail=f"Ya existe el nivel «{repetido or nivel_id}».")
    nivel = Nivel(id=nivel_id, estado="borrador", actualizado_en=ahora(), **cuerpo.model_dump())
    db.add(nivel)
    confirmar(db, "/api/contenido/niveles")
    return nivel_meta(nivel)


@router.put("/niveles/{nivel_id}")
def editar_nivel(nivel_id: str, cuerpo: CambiosNivel, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    nivel = obtener_nivel(db, nivel_id)
    for campo, valor in cuerpo.model_dump(exclude_unset=True).items():
        if campo in ("titulo", "estado") and valor is None:
            continue
        setattr(nivel, campo, valor)
    nivel.actualizado_en = ahora()
    confirmar(db, f"/api/contenido/niveles/{nivel_id}")
    return nivel_meta(nivel)


def sembrar_niveles_blender(db: Session) -> List[str]:
    """Crea en borrador los niveles de NIVELES_BLENDER que falten. No hace commit.

    No toca los que ya existen (pudieron editarse en el panel o en Oracle).
    """
    if db.get(Curso, "blender") is None:
        return []
    creados = []
    for datos in NIVELES_BLENDER:
        if db.get(Nivel, datos["id"]) is None:
            db.add(Nivel(curso_id="blender", estado="borrador", actualizado_en=ahora(), **datos))
            creados.append(datos["id"])
    db.flush()
    return creados


@router.post("/niveles/sembrar")
def sembrar(db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    if db.get(Curso, "blender") is None:
        raise HTTPException(status_code=409, detail="Falta el curso «blender»: importa antes el catálogo.")
    creados = sembrar_niveles_blender(db)
    confirmar(db, "/api/contenido/niveles/sembrar")
    return {"creados": creados}


@router.get("/mapa/{curso_id}")
def mapa(curso_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(lector)):
    """Curso > nivel > módulo > lección, con lo que falta en cada ficha.

    Los módulos sin nivel aparecen en «sin_nivel». La ficha incompleta no
    impide publicar (las lecciones anteriores a la v3 no la tienen): sirve
    para ver de un vistazo qué falta escribir.
    """
    curso = obtener_curso(db, curso_id)
    requiere_blender = curso.id == "blender"
    niveles = list(db.scalars(select(Nivel).where(Nivel.curso_id == curso.id).order_by(Nivel.numero, Nivel.rama)))
    modulos = list(db.scalars(select(Modulo).where(Modulo.curso_id == curso.id).order_by(Modulo.numero, Modulo.id)))
    lecciones: Dict[str, List[dict]] = defaultdict(list)
    for fila in db.scalars(select(Leccion).where(Leccion.curso_id == curso.id).order_by(Leccion.orden, Leccion.id)):
        contenido = leer_json(fila.contenido)
        ficha = contenido.get("ficha") if isinstance(contenido, dict) else None
        lecciones[fila.modulo_id].append(
            {
                "id": fila.id,
                "orden": fila.orden,
                "titulo": fila.titulo,
                "estado": fila.estado,
                "version": fila.version,
                "objetivo": ficha.get("objetivo") if isinstance(ficha, dict) else None,
                "habilidades": ficha.get("habilidades") if isinstance(ficha, dict) else [],
                "blender": (ficha.get("blender") or {}).get("verificadaEn") if isinstance(ficha, dict) else None,
                "pendientes": pendientes_ficha(contenido, requiere_blender),
            }
        )

    def modulo_mapa(modulo: Modulo) -> dict:
        suyas = lecciones[modulo.id]
        return {
            "id": modulo.id,
            "numero": modulo.numero,
            "titulo": modulo.titulo,
            "estado": modulo.estado,
            "lecciones": suyas,
            "fichas_completas": sum(not x["pendientes"] for x in suyas),
        }

    por_nivel: Dict[Optional[str], List[dict]] = defaultdict(list)
    for modulo in modulos:
        por_nivel[modulo.nivel_id].append(modulo_mapa(modulo))
    total = sum(len(x) for x in lecciones.values())
    completas = sum(not x["pendientes"] for suyas in lecciones.values() for x in suyas)
    return {
        "curso": {"id": curso.id, "titulo": curso.titulo},
        "niveles": [{**nivel_meta(n), "modulos": por_nivel.get(n.id, [])} for n in niveles],
        "sin_nivel": por_nivel.get(None, []),
        "resumen": {"lecciones": total, "fichas_completas": completas},
    }
