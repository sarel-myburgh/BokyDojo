#!/bin/sh
# =============================================================================
# DojoMaster container entrypoint
#
# The image has to be able to stand up on its own: `podman run` against an empty
# database previously started gunicorn against unmigrated tables and an empty
# static root, which fails in two different confusing ways at once.
#
# ⚠ Waits for the database rather than assuming it. compose `depends_on` with a
# healthcheck covers the ordinary case, but `podman-compose` honours it less
# reliably than docker compose does, and a restarted database outlives the
# healthcheck window. Retrying here costs nothing and removes a whole class of
# "it works on the second try".
# =============================================================================
set -eu

RUN_MIGRATIONS="${DOJOMASTER_MIGRATE:-true}"
WAIT_SECONDS="${DOJOMASTER_DB_WAIT:-60}"

wait_for_database() {
    echo "entrypoint: waiting up to ${WAIT_SECONDS}s for the database..."
    elapsed=0
    while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
        # ⚠ Django's own connection, not pg_isready: the runtime image installs
        # no postgres client, and this also proves the credentials and database
        # name are right rather than merely that a port is open.
        if python -c "
import django, sys
django.setup()
from django.db import connections
try:
    connections['default'].cursor()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
            echo "entrypoint: database is up."
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "entrypoint: database did not become available within ${WAIT_SECONDS}s." >&2
    return 1
}

wait_for_database

if [ "$RUN_MIGRATIONS" = "true" ]; then
    # ⚠ Only the web container does this; the worker sets DOJOMASTER_MIGRATE=false.
    # Two containers racing to migrate the same database is how a self-host
    # deployment ends up with a half-applied schema.
    echo "entrypoint: applying migrations..."
    python manage.py migrate --noinput

    echo "entrypoint: collecting static files..."
    python manage.py collectstatic --noinput --clear
else
    echo "entrypoint: DOJOMASTER_MIGRATE is not 'true' — skipping migrate/collectstatic."
fi

echo "entrypoint: starting: $*"
exec "$@"
