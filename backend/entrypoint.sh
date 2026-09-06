#!/bin/sh
set -eu

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py replayobservations
python manage.py bootstrap_admin

# PID 1 forwards SIGTERM to every child. The CPA collector handles it
# explicitly so it can flush its durable spool and record the closing boundary.
python manage.py runmonitor &
monitor_pid=$!
python manage.py runcpacollector &
collector_pid=$!
python manage.py runresearch &
research_pid=$!
gunicorn pinche.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --access-logfile - --error-logfile - &
web_pid=$!

shutdown() {
    trap - TERM INT
    kill -TERM "$monitor_pid" "$collector_pid" "$research_pid" "$web_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
    wait "$collector_pid" 2>/dev/null || true
    wait "$research_pid" 2>/dev/null || true
    wait "$web_pid" 2>/dev/null || true
}

on_signal() {
    shutdown
    exit 0
}

trap on_signal TERM INT
set +e
wait "$web_pid"
status=$?
set -e
shutdown
exit "$status"
