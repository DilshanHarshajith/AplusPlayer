# Aplus Player web service — multi-user production image.
#
# Build:    docker build -t aplus-player .
# Run:      see docker-compose.yml (recommended) or:
#           docker run -p 5000:5000 -e FLASK_SECRET_KEY=$(openssl rand -hex 32) \
#             -v aplus_state:/data aplus-player
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg is used to remux downloaded .ts streams to .mp4 (optional but nice).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application (secrets like .env are excluded via .dockerignore).
COPY . .

# Create an unprivileged user for runtime. When running via docker-compose,
# the `user:` directive in docker-compose.yml overrides this and ensures the
# container runs as the host's UID/GID, avoiding SQLite file-ownership
# conflicts on the mounted ./data volume.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
