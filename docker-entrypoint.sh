#!/bin/sh
# Runs on every container start, before the web server.
# Both steps are safe to repeat: migrate applies only what is missing and
# ensure_admin creates the admin user only if it does not exist yet.
set -e

python manage.py migrate --noinput
python manage.py ensure_admin

exec "$@"
