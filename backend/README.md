# Amatista · Backend

API de FastAPI que guarda las sesiones, el progreso, el enlace en vivo con Blender, el modelo por habilidad de Amatista Taller, los avatares y el buzón del creador en Oracle Autonomous Database (o SQLite en desarrollo y pruebas).

La app funciona aunque este backend esté caído: el progreso se guarda primero en el dispositivo del alumno (IndexedDB) y se sincroniza aquí cuando el servidor responde.

## Rutas (14 routers en `main.py`)

Detalle de cada contrato en `docs/manual-del-codigo/03_backend.md`. Los errores siempre responden `{"detail": "mensaje en español"}`.

| Grupo | Rutas | Para qué |
|---|---|---|
| Salud | `GET /` · `GET /api/salud` | Backend vivo · base de datos responde (503 si no) |
| Cuentas (`api/auth.py`) | `POST /api/auth/registro`, `iniciar-sesion`, `cerrar-sesion`, `cerrar-todas` · `GET/PATCH /api/auth/yo` · `POST confirmar-correo`, `reenviar-codigo`, `recuperar`, `restablecer`, `cambiar-password` | Registro, sesión con token, confirmación por correo y recuperación |
| Sesiones heredadas (`api/sesiones.py`) | `POST /api/iniciar-sesion` | Compatibilidad con alumnos anónimos |
| Progreso (`api/progreso.py`) | `POST /api/progreso` · `GET /api/progreso` (`?desde=` incremental) · `GET /api/progreso/{usuario_id}` | Guardar y leer progreso; las cuentas `admin` practican sin guardar progreso |
| Eventos (`api/eventos.py`) | `POST /api/eventos` | Eventos de aprendizaje deduplicados por id |
| Sincronización comprimida (`api/sincronizar.py`) | `POST /api/sincronizar` | Recibe progreso + eventos en una sola petición (acepta `Content-Encoding: gzip`) y devuelve el progreso fusionado; pensado para escuelas con pocos datos ([17_pocos_datos.md](../docs/plataforma/17_pocos_datos.md)) |
| Contenido (`api/contenido.py`) | `GET /api/contenido/catalogo` (público, ETag) · `admin/arbol`, `plantillas`, `validar`, CRUD de cursos, módulos y lecciones, `publicar`, `archivar`, `mover`, `exportar` | Contenido administrable borrador → publicado |
| Niveles (`api/niveles.py`) | `GET/POST /api/contenido/niveles` · `PUT /niveles/{id}` · `POST /niveles/sembrar` · `GET /api/contenido/mapa/{curso_id}` | Niveles del curso (v3) y mapa de fichas pendientes |
| Blender (`api/blender.py`) | `GET /api/blender/versiones` · `PUT /versiones/{v}` · `POST /verificaciones` · `GET /compatibilidad` | Versiones de Blender verificadas y matriz de compatibilidad |
| Add-on de Blender (`api/addon.py`) | `/api/addon/v1`: `estado`, `vinculos`, `yo`, `salir`, `dispositivos`, `practicas`, `practica-actual`, `intentos`, `mi-progreso`, `descargas/{sistema}` (`.exe`, `.deb`, `.zip`), `extension.zip`, `extensiones/index.json` | Vincular Blender, prácticas del motor, instaladores y progreso ([05_api.md](../docs/motor/referencia/05_api.md)) |
| Enlace en vivo (`api/enlace.py`) | `/api/addon/v1`: `POST/GET /enlace`, `POST /ordenes`, `GET/PUT /ajustes` | Latido de Blender, cola de órdenes (`leccion`, `recorrido`, `abrir_practica`, `taller`, etc.) y ajustes de «Mi Blender» (`sql/010` y `sql/011`) |
| Amatista Taller (`api/taller.py`) | `/api/addon/v1`: `GET /habilidades`, `POST /habilidades/intentos`, `GET /talleres`, `GET /talleres/{taller_id}` | Modelo BKT del alumno por habilidad e isla (`sql/012`) |
| Perfil y avatares (`api/perfil.py`) | `GET /api/perfil/avatar` · `PUT /api/perfil/avatar` | Personaje low poly de cada cuenta y pavo real exclusivo de `AMATISTA_CREADOR` (`sql/013`) |
| Buzón del creador (`api/buzon.py`) | `GET/POST /api/buzon`, `POST /api/buzon/{id}/mensajes` · `GET /api/admin/buzon`, `POST /api/admin/buzon/{id}/responder`, `PATCH /api/admin/buzon/{id}` | Conversaciones directas de los alumnos con el creador (`sql/013`) |
| Administración (`api/admin.py`) | `GET /api/admin/resumen`, `usuarios`, `usuarios/{id}`, `auditoria`, `salud-detallada` · `PATCH usuarios/{id}` · `POST mantenimiento/purgar` | Métricas, auditoría y gestión de usuarios |

## Probar en local sin Oracle (SQLite)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///./amatista_local.db uvicorn main:app --reload
```

Pruebas automáticas y validación de contenido:

```bash
python -m pytest -q
python herramientas/contenido.py validar
```

## Actualizar Oracle en el servidor (`sql/001` a `013`, 24 tablas)

El orden exacto y las reglas viven en [`sql/LEEME.txt`](sql/LEEME.txt):
- **Base existente con datos**: `002` → `003` → `005` → `006` → `007` → `008` → `010` → `011` → `012` → `013` → `004` (opcional) → `009` (**nunca `001`**).
- **Verificar antes de reiniciar**: `python diagnostico_oracle.py` debe responder `✓ Las tablas coinciden con lo que espera el backend`.
