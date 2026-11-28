# backend/Dockerfile — API de Amatista con usuario sin privilegios (UID 10001)
FROM python:3.11-slim AS ejecutor

RUN groupadd -g 10001 amatista && \
    useradd -u 10001 -g amatista -m -s /usr/sbin/nologin amatista

WORKDIR /app

# [REVISION-BUG] Instala requirements.txt (rangos >=, sin fijar) y no requirements.lock como CI y producción; tampoco pyyaml/Pillow
# que CI instala aparte. Python 3.11 aquí vs 3.12 en CI.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=amatista:amatista backend/ ./backend/
# [REVISION-ROTO] practices/ no existe en este repositorio (vive en amatista-motor-): 'docker build' falla aquí. Además la imagen no
# incluye el motor (engine/, addon/) que backend/vecinos.py exige vía AMATISTA_MOTOR_DIR o carpeta hermana: la API no arrancaría.
# No hay ningún flujo que construya esta imagen (CI y la VM usan venv + systemd): decidir si se arregla o se retira.
COPY --chown=amatista:amatista practices/ ./practices/

USER amatista:amatista
WORKDIR /app/backend

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
