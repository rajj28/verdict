FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    STATIC_ROOT=/app/static \
    DJANGO_SETTINGS_MODULE=verdict.settings

WORKDIR /app

# Fully offline build: every Python package is vendored in vendor/wheels
# (Linux x86_64 and aarch64, CPython 3.12) and pip is forbidden from reaching
# an index, so the build works with the network off. docker-compose.yml also
# builds with network: none to prove it. Regenerate with scripts/vendor_wheels.py.
COPY requirements.txt ./
COPY vendor/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt     && rm -rf /tmp/wheels

COPY . .

# Non-root, and /data is where the secret key and uploaded media live. The
# static root is baked into the image: /data is a volume at runtime, so
# collectstatic output there would be invisible once the container starts.
RUN useradd --create-home --uid 10001 verdict \
    && mkdir -p /data /app/static \
    && chown -R verdict:verdict /data /app
USER verdict

# A throwaway key: the real one comes from SECRET_KEY or is generated on first
# boot into /data/secret_key. No build secret is baked in. DATABASE_URL points
# at an unroutable port so collectstatic fails fast instead of hitting a real
# database if it ever tries to connect; it is build-only, not a runtime default.
RUN SECRET_KEY=collectstatic-only DATABASE_URL=postgres://build:build@127.0.0.1:1/build python manage.py collectstatic --noinput

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=3)"]

CMD ["python", "manage.py", "runportal"]
