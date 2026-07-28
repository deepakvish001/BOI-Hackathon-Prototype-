# BODHI MULE HUNTER AI - self-contained image.
#
# The engine deliberately has no GPU, CUDA or deep-learning-framework
# dependency, so this is a slim CPU image that starts anywhere.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BODHI_ARTIFACTS=/app/artifacts

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pyarrow \
 && apt-get purge -y build-essential && apt-get autoremove -y

COPY bodhi/ ./bodhi/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY Makefile pyproject.toml README.md ./

# Bake in the simulated bank and the trained models so the container starts
# ready to serve rather than training on first request.
RUN python scripts/generate_data.py --accounts 6000 --days 90 \
 && python scripts/train.py --quiet \
 && python scripts/make_sample_apk.py

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "bodhi.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
