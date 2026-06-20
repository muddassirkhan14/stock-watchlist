FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7080

# Debian trust store (needed for update-ca-certificates and corporate roots)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional build-time corporate roots: add *.crt or *.pem under docker/corp-ca/
# (see docker/corp-ca/README.txt). Skips non-certificate files.
COPY docker/corp-ca/ /tmp/corp-ca-builtin/
RUN set -e; \
    for f in /tmp/corp-ca-builtin/*; do \
      [ -f "$f" ] || continue; \
      case "$f" in \
        *.crt|*.pem) \
          bn=$(basename "$f"); \
          cp "$f" "/usr/local/share/ca-certificates/corp-builtin-$bn.crt" ;; \
        *) ;; \
      esac; \
    done; \
    if ls /usr/local/share/ca-certificates/corp-builtin-*.crt >/dev/null 2>&1; then \
      update-ca-certificates; \
    fi; \
    rm -rf /tmp/corp-ca-builtin

COPY requirements.txt .
# Default matches docker-compose so plain `docker build` also works behind SSL inspection.
# Pass BUILD_PIP_TRUSTED_HOST=strict (build-arg or .env) for full pip TLS verification.
ARG BUILD_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
RUN set -e; \
    if [ "$BUILD_PIP_TRUSTED_HOST" != "strict" ]; then \
      export PIP_TRUSTED_HOST="$BUILD_PIP_TRUSTED_HOST"; \
    fi; \
    pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY gunicorn.conf.py .
COPY core ./core
COPY frontend ./frontend

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

RUN mkdir -p data

EXPOSE 7080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "-b", "0.0.0.0:7080", "-w", "1", "--threads", "8", "app:app"]
