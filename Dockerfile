# Backend image (FastAPI + agent + MCP server) for Railway or any container host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    TRAVEL_APP_DB=/data/travel_app.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY server ./server
COPY evaluations ./evaluations
COPY benchmarks ./benchmarks
COPY benchmark.py .

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

# Railway injects PORT; it defaults to 8000 elsewhere.
CMD ["python", "-m", "server"]
