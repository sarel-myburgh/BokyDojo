#!/usr/bin/env bash
# Start a disposable local DojoMaster demo for hands-on testing.
#
# This deliberately uses config.settings.dev (SQLite, no Docker services) and
# refuses an explicit PostgreSQL connection. It is not a production launcher.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

host="${DOJOMASTER_HOST:-127.0.0.1}"
port="${DOJOMASTER_PORT:-8000}"
reset_demo=1

usage() {
  cat <<'EOF'
Usage: bash start.sh [--keep-data]

Starts the local development server at http://127.0.0.1:8000.
By default it resets and seeds the local SQLite demo database. Use
--keep-data to preserve the current demo data.

Optional environment variables:
  DOJOMASTER_HOST       Bind address (default: 127.0.0.1)
  DOJOMASTER_PORT       Port (default: 8000)
  DOJOMASTER_PYTHON     Python executable to use
  DOJOMASTER_WSL_VENV   Linux venv location when run from WSL
EOF
}

for argument in "$@"; do
  case "$argument" in
    --keep-data) reset_demo=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "${POSTGRES_HOST:-}" ]]; then
  echo "Refusing to start: POSTGRES_HOST is set." >&2
  echo "This launcher is only for the disposable SQLite dry-run database." >&2
  exit 1
fi

if [[ -n "${DOJOMASTER_PYTHON:-}" ]]; then
  python_bin="$DOJOMASTER_PYTHON"
elif grep -qi microsoft /proc/version 2>/dev/null; then
  # A Windows virtual environment cannot run under WSL. Keep the Linux venv
  # outside /mnt/c, where Linux executables and permissions behave normally.
  venv_dir="${DOJOMASTER_WSL_VENV:-$HOME/.cache/dojomaster-venv}"
  python_bin="$venv_dir/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    command -v python3 >/dev/null || {
      echo "Python 3 is required to create the WSL virtual environment." >&2
      exit 1
    }
    echo "Creating WSL virtual environment at $venv_dir..."
    python3 -m venv "$venv_dir"
    "$python_bin" -m pip install --upgrade pip
    "$python_bin" -m pip install -e '.[dev]'
  fi
elif [[ -x .venv/bin/python ]]; then
  python_bin=".venv/bin/python"
elif [[ -x .venv/Scripts/python.exe ]]; then
  python_bin=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null; then
  python_bin="$(command -v python3)"
elif command -v python >/dev/null; then
  python_bin="$(command -v python)"
else
  echo "Python 3.12 or newer is required. Create .venv, then retry." >&2
  exit 1
fi

if ! "$python_bin" -c 'import django' 2>/dev/null; then
  echo "Django is not installed for $python_bin." >&2
  echo "Install the project dependencies first: $python_bin -m pip install -e '.[dev]'" >&2
  exit 1
fi

export DJANGO_SETTINGS_MODULE=config.settings.dev

echo "Applying migrations..."
"$python_bin" manage.py migrate --noinput

if [[ "$reset_demo" -eq 1 ]]; then
  echo "Resetting and seeding the local demo database..."
  "$python_bin" manage.py seed --clear
else
  echo "Keeping existing local demo data."
fi

echo "Starting DojoMaster at http://$host:$port (Ctrl+C to stop)."
exec "$python_bin" manage.py runserver "$host:$port"
