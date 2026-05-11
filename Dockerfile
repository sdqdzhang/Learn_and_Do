# ============================================================
# Tiny-Devin-Base
# Lightweight execution sandbox shared by both task modes:
#   - DEVELOPMENT (run / test generated code)
#   - PHILOSOPHY  (run scraping / analysis / modeling scripts)
# Agents may still `pip install` extra packages at runtime; this
# image only ships the libraries that almost every task needs.
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install \
        requests \
        httpx \
        beautifulsoup4 \
        lxml \
        numpy \
        pandas \
        matplotlib \
        scipy \
        pytest

WORKDIR /workspace

# The executor overrides CMD when running concrete scripts; this default
# is only used for ad-hoc `docker run -it` debugging.
CMD ["python", "-i"]
