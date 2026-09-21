FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY risk_platform ./risk_platform
RUN pip install --no-cache-dir uv==0.11.31 && uv sync --frozen --all-extras --no-cache && useradd --uid 10001 --create-home risk
COPY scripts/cloud_run.py ./scripts/cloud_run.py
COPY warehouse ./warehouse
USER 10001
CMD ["python", "scripts/cloud_run.py"]
