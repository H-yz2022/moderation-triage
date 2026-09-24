# --- build the React UI -------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- python runtime ------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY src/ src/
COPY policy/ policy/
COPY data/sample/ data/sample/
RUN pip install --no-cache-dir --no-deps -e .
COPY --from=ui /ui/dist frontend/dist
# A trained gate (data/models/) and eval report (reports/) are optional; mount or
# COPY them in if you want them in the deployed image.
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn modtriage.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
