# Amatista · Backend

API de FastAPI que guarda las sesiones y el progreso de los alumnos en
Oracle Autonomous Database.

La app funciona aunque este backend esté caído: el progreso se guarda
primero en el dispositivo del alumno (IndexedDB) y se sincroniza aquí
cuando el servidor responde.

## Rutas

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/` | Saber si el backend está vivo |
| GET | `/api/salud` | Comprobar que la base de datos responde (503 si no) |
| POST | `/api/iniciar-sesion` | Registrar una sesión `{email o usuario_id, dispositivo}` |
| POST | `/api/progreso` | Guardar progreso `{usuario_id, eventos: [...]}` |
| GET | `/api/progreso/{usuario_id}` | Leer el progreso de un alumno |

La documentación interactiva queda en `http://<servidor>:8000/docs`.

## Probar en tu computadora sin Oracle (SQLite)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///./amatista_local.db uvicorn main:app --reload
```

En otra terminal, `cd frontend && npm run dev`. El frontend usa
`VITE_API_URL` de `frontend/.env` (ver `frontend/.env.example`).

Pruebas automáticas: `python -m pytest`.

## Dejar Oracle funcionando (en el servidor ARM)

La base solo acepta conexiones desde la IP del servidor (ACL), así que
estos pasos se hacen ahí.

1. **Respaldar y actualizar.** El código que hoy corre en el servidor no
   está en el repositorio, así que primero se guarda una copia:
   ```bash
   cp -r ~/amatista/backend ~/backend_respaldo_$(date +%F)
   cd ~/amatista
   git stash --include-untracked   # aparta los cambios locales del servidor
   git pull origin main
   ```
   `git stash` no toca `backend/.env` ni `venv/` (están en `.gitignore`).
2. **Instalar dependencias:** `cd backend && source venv/bin/activate && pip install -r requirements.txt`
3. **Revisar la conexión.** Tu `backend/.env` actual sirve tal cual si tiene
   `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` y `DB_SERVICE`: el backend
   arma la conexión cifrada (`tcps`) con ellos. Si no existe, créalo con
   `cp .env.example .env`. Si falla, pega en `DB_DSN` la cadena **TLS** de la
   consola de OCI (Autonomous Database → Conexión a la base de datos).
4. **Recrear las tablas:** abre Database Actions → SQL, pega
   `sql/001_esquema_amatista.sql` y pulsa **Ejecutar script (F5)**. Esto
   borra los datos de prueba.
5. **Verificar:** `python diagnostico_oracle.py`. Debe terminar con
   «✓ Las tablas coinciden con lo que espera el backend».
6. **Arrancar:** `uvicorn main:app --host 0.0.0.0 --port 8000` y abre
   `http://<servidor>:8000/api/salud`. Debe responder `{"estado": "ok", "motor": "oracle"}`.

### Dejarlo como servicio (evita el error del puerto ocupado, Errno 98)

`/etc/systemd/system/amatista-backend.service`:

```ini
[Unit]
Description=Amatista API
After=network-online.target

[Service]
User=opc
WorkingDirectory=/home/opc/amatista/backend
ExecStart=/home/opc/amatista/backend/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Cambia `opc` por tu usuario del servidor (en Ubuntu suele ser `ubuntu`). Luego:
`sudo systemctl daemon-reload && sudo systemctl enable --now amatista-backend`.
Para ver los errores: `journalctl -u amatista-backend -f`.

## Errores de Oracle frecuentes

| Error | Causa real | Solución |
|---|---|---|
| ORA-00942 table or view does not exist | La tabla no existe con ese nombre (o se creó con comillas en minúsculas) | Ejecutar el script SQL |
| ORA-01722 invalid number | Se guarda texto (correo, UUID) en una columna NUMBER | Todas las columnas de usuario son VARCHAR2 en el script |
| ORA-02267 column type incompatible with referenced column type | Llave foránea entre columnas de tipos distintos (VARCHAR2 → NUMBER) | Mismo tipo en ambos lados: lo hace el script |
| ORA-12506 / timeout | La ACL no incluye la IP desde donde te conectas | Ejecutar desde el servidor o agregar tu IP en OCI |
| Errno 104 Connection reset | Conexión sin cifrar al puerto 1522 | Usar la cadena TLS (`tcps`) |
