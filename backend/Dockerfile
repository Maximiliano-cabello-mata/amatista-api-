# backend/Dockerfile — API de Amatista con usuario sin privilegios (UID 10001)
FROM python:3.11-slim AS ejecutor

RUN groupadd -g 10001 amatista && \
    useradd -u 10001 -g amatista -m -s /usr/sbin/nologin amatista

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=amatista:amatista backend/ ./backend/
COPY --chown=amatista:amatista practices/ ./practices/

USER amatista:amatista
WORKDIR /app/backend

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
