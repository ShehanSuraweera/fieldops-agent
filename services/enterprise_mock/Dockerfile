FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8000
# Migrate, seed a fresh database, then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed --if-empty && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
