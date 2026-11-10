# Amatista · API

La API de [Amatista](https://amatista-3dt.tech): cuentas, progreso, contenido, clases, evaluación y la base de datos. También sirve al complemento de Blender (vinculación, prácticas, descargas y enlace en vivo).

- Producción: `https://api.amatista-3dt.tech`
- Se separó del monorepo el 10 de noviembre de 2026.

| Carpeta | Qué es |
|---|---|
| [`backend/`](backend/) | La aplicación FastAPI. `main.py` arma la aplicación; cada grupo de rutas vive en `api/`; los modelos en `database/`; los scripts de Oracle en `sql/` |
| [`backend/herramientas/`](backend/herramientas/) | Contenido, migración entre bases, administradores, rendimiento y auditoría |
| [`despliegue/`](despliegue/) | El comando `amatista`, la unidad de systemd, Caddy y el script de actualización |
| [`docs/`](docs/) | Base de datos y despliegue |

## Las API que sirve

Un proceso, cinco API. Cada una vive en su paquete y firma sus errores con su nombre. Detalle y formato de errores: [docs/api/README.md](docs/api/README.md).

| API | Paquete | Prefijo | Para quién |
|---|---|---|---|
| `plataforma` | `backend/api/plataforma/` | `/api/auth`, `/api/contenido`, `/api/perfil`, `/api/buzon`, `/api/blender`, `/api` | La web del alumno |
| `motor` | `backend/api/motor/` | `/api/addon/v1` | El complemento de Blender |
| `aula` | `backend/api/aula/` | `/api/evaluacion` | Panel del profesor y «Mi clase» |
| `administracion` | `backend/api/administracion/` | `/api/admin` | Panel de administración |
| `datos` | `backend/api/datos/` | `/api/g7b/v1` | Geoda |

Un error dice de dónde sale: `{"detail": "...", "error": {"api": "motor", "operacion": "leer_practica", "codigo": "motor.leer_practica.no_encontrado", "estado": 404}}`, y toda respuesta lleva `X-Peticion-Id`.

## Responsabilidad

Este repositorio decide **qué se guarda y quién puede verlo**. No decide si una práctica está bien hecha: para eso importa el motor.

| Repositorio | Responsabilidad | Relación con la API |
|---|---|---|
| `amatista-api-` (este) | API y base de datos | — |
| [`amatista-motor-`](https://github.com/Maximiliano-cabello-mata/amatista-motor-) | Motor, complemento y prácticas | **Obligatorio.** La API importa `amatista_engine`, lee `practices/` y arma las descargas del complemento |
| Web (hoy en el monorepo) | Lo que ven el alumno, el profesor y el administrador | Consume la API por HTTP. Sus lecciones se validan con `herramientas/contenido.py` |
| [`amatista-g7b-etl`](https://github.com/Maximiliano-cabello-mata/amatista-g7b-etl) | Datos de rendimiento para Geoda | La API publica su estado agregado |

[`backend/vecinos.py`](backend/vecinos.py) busca los otros repositorios en una carpeta hermana o en `AMATISTA_MOTOR_DIR` y `AMATISTA_WEB_DIR`.

## Empezar

Con Python 3.12, y el motor clonado al lado:

```bash
git clone https://github.com/Maximiliano-cabello-mata/amatista-motor- ../amatista-motor-
```

```bash
cd backend && python -m venv venv && venv/bin/pip install -r requirements-dev.txt
```

```bash
DATABASE_URL=sqlite:///./amatista_local.db venv/bin/uvicorn main:app --reload
```

Pruebas, dentro de `backend/`:

```bash
venv/bin/python -m pytest -q
```

Sin el repositorio de la web, nueve pruebas de contrato que leen las lecciones se saltan y dicen qué falta. Con `AMATISTA_WEB_DIR` apuntando al repositorio que contiene `frontend/` corren todas.

## En la instancia de Oracle

Un solo comando: [`despliegue/amatista`](despliegue/amatista). Guía: [docs/despliegue/2026-11-10_comando_amatista.md](docs/despliegue/2026-11-10_comando_amatista.md).

```bash
amatista actualizar
```

## Historia

La historia sale de filtrar `backend/` y `despliegue/` del monorepo (del 27 de septiembre al 5 de noviembre de 2026). Los identificadores de commit son nuevos; los originales están en la rama `historia/vm-main` del monorepo.

Maximiliano Cabello Mata dirige el proyecto.
