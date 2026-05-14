FROM python:3.11-slim

LABEL org.opencontainers.image.title="HEATWISE HySUPP Unmixing Processor"
LABEL org.opencontainers.image.description="EOAP-oriented supervised FCLS unmixing processor for CHIME-mimicked hyperspectral products"
LABEL org.opencontainers.image.version="1.0.0"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir -r /app/requirements.txt

COPY processor.py /app/processor.py
COPY src /app/src

ENTRYPOINT ["python", "/app/processor.py"]
