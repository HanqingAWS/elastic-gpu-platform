FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /app/
RUN uv pip install --system strands-agents boto3 fastapi "uvicorn[standard]" pydantic tzdata
COPY app /app/app
ENV PYTHONUNBUFFERED=1
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -f http://localhost:8100/health || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
