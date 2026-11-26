FROM python:3.12-slim

# No .pyc files in the image, and log lines appear immediately in `docker compose logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SQLITE_PATH=/app/db/db.sqlite3

WORKDIR /app

# Dependencies first: this layer is rebuilt only when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Admin CSS/JS is collected at build time and served by WhiteNoise.
# The app runs as a normal user; /app/db is where the SQLite volume is mounted
# (a new named volume inherits this directory's owner).
RUN python manage.py collectstatic --noinput \
    && useradd --create-home app \
    && mkdir -p /app/db /app/data \
    && chown app:app /app/db
USER app

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
