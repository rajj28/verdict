FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    STATIC_ROOT=/app/static \
    DJANGO_SETTINGS_MODULE=verdict.settings

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root, and /data is where the secret key and uploaded media live. The
# static root is baked into the image: /data is a volume at runtime, so
# collectstatic output there would be invisible once the container starts.
RUN useradd --create-home --uid 10001 verdict \
    && mkdir -p /data /app/static \
    && chown -R verdict:verdict /data /app
USER verdict

# A throwaway key: the real one comes from SECRET_KEY or is generated on first
# boot into /data/secret_key. No build secret is baked in.
RUN SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=3)"]

CMD ["python", "manage.py", "runportal"]
