#!/usr/bin/env bash
# Start a disposable local BokyDojo demo for hands-on testing.
#
# This deliberately uses config.settings.dev (SQLite, no Docker services) and
# refuses an explicit PostgreSQL connection. It is not a production launcher.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

host="${BOKYDOJO_HOST:-127.0.0.1}"
port="${BOKYDOJO_PORT:-8000}"
reset_demo=1

usage() {
  cat <<'EOF'
Usage: bash start.sh [--keep-data]

Starts the local development server at http://127.0.0.1:8000.
By default it resets and seeds the local SQLite demo database. Use
--keep-data to preserve the current demo data.

Optional environment variables:
  BOKYDOJO_HOST       Bind address (default: 127.0.0.1)
  BOKYDOJO_PORT       Port (default: 8000)
  BOKYDOJO_PYTHON     Python executable to use
  BOKYDOJO_VENV       Virtual environment location when one must be created
                        (default: $HOME/.cache/bokydojo-venv)
  BOKYDOJO_WSL_VENV   Deprecated alias for BOKYDOJO_VENV
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

# ⚠ Test that an interpreter actually *runs* before trusting it. A checkout made
# on Windows carries .venv/Scripts/python.exe, and on a DrvFs or NTFS mount every
# file reads as mode 777 — so `[[ -x ]]` happily says yes to a Windows binary on
# Linux, and the script then advises you to run a .exe to fix it. Executing it is
# the only honest test.
runnable() {
  [[ -n "${1:-}" && -x "$1" ]] && "$1" -c '' >/dev/null 2>&1
}

# Create the out-of-tree virtual environment, and get pip into it.
create_venv() {
  local venv_dir="$1"
  command -v python3 >/dev/null || {
    echo "Python 3 is required to create a virtual environment." >&2
    exit 1
  }
  echo "Creating virtual environment at $venv_dir..."
  rm -rf "$venv_dir"
  # Output is suppressed because a failure here is recoverable: the stock message
  # ends "You may need to use sudo", which is alarming and wrong when the very
  # next branch succeeds without it.
  if python3 -m venv "$venv_dir" >/dev/null 2>&1; then
    return 0
  fi
  # ⚠ Debian and Ubuntu ship `venv` without `ensurepip` — it lives in a separate
  # pythonX.Y-venv package — so `python3 -m venv` fails outright on an otherwise
  # healthy machine, and does so most often on a box with no sudo to fix it with.
  # Build the environment without pip and bootstrap pip into it instead.
  echo "  ensurepip is unavailable; bootstrapping pip separately." >&2
  rm -rf "$venv_dir"
  python3 -m venv --without-pip "$venv_dir" || {
    echo "Could not create a virtual environment. Install your distribution's" >&2
    echo "python3-venv package (e.g. apt install python3-venv), then retry." >&2
    exit 1
  }
  local get_pip
  get_pip="$(mktemp)"
  if ! curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$get_pip"; then
    rm -f "$get_pip"
    echo "Could not download get-pip.py, and this Python has no ensurepip." >&2
    echo "Install your distribution's python3-venv package, then retry." >&2
    exit 1
  fi
  "$venv_dir/bin/python" "$get_pip" >/dev/null
  rm -f "$get_pip"
}

if [[ -n "${BOKYDOJO_PYTHON:-}" ]]; then
  python_bin="$BOKYDOJO_PYTHON"
elif runnable .venv/bin/python; then
  python_bin=".venv/bin/python"
elif runnable .venv/Scripts/python.exe; then
  python_bin=".venv/Scripts/python.exe"
else
  # No in-tree environment this platform can run — which covers native Linux, and
  # WSL looking at a Windows .venv. Under WSL the environment must live outside
  # /mnt/c, where Linux executables and permissions behave normally; $HOME is
  # already there, so the same default works for both.
  venv_dir="${BOKYDOJO_VENV:-${BOKYDOJO_WSL_VENV:-$HOME/.cache/bokydojo-venv}}"
  python_bin="$venv_dir/bin/python"
  if ! runnable "$python_bin"; then
    create_venv "$venv_dir"
    "$python_bin" -m pip install --upgrade pip
    "$python_bin" -m pip install -e '.[dev]'
  fi
fi

if ! runnable "$python_bin"; then
  echo "Not a usable Python interpreter for this platform: $python_bin" >&2
  exit 1
fi

if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Python 3.12 or newer is required; $python_bin is older." >&2
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

echo "Starting BokyDojo at http://$host:$port (Ctrl+C to stop)."
exec "$python_bin" manage.py runserver "$host:$port"
