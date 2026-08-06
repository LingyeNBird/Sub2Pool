#!/bin/sh
set -eu

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py bootstrap_admin

# 轮询器与 Web 共用 SQLite 和业务代码。无需 Redis/Celery；容器停止时 Docker 会终止整个进程组。
python manage.py runmonitor &
exec gunicorn pinche.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --access-logfile - --error-logfile -
