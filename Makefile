# =============================================================================
# BokyDojo — Makefile
# TODO 0.1.7 — dev, test, lint, migrate, seed, backup, restore
# =============================================================================

.PHONY: dev test lint format migrate seed backup restore shell check venv install

# -- Interpreter ---------------------------------------------------------------
#
# ⚠ Every target runs tools as `$(PYTHON) -m <tool>`, never as a bare `pytest` or
# `ruff`. Bare names only resolve inside an *activated* virtual environment, and
# on a plain Linux box `python` frequently does not exist at all — only `python3`.
# This file used to assume both, so every documented command failed with
# "pytest: not found" on a machine where the suite was in fact perfectly green.
#
# Resolution order: an explicit PYTHON=..., an in-tree .venv this platform can
# actually run, the out-of-tree environment start.sh builds, then python3.
# A checkout made on Windows carries a .venv/Scripts/python.exe that is marked
# executable on Linux, so the interpreter is *executed* to prove it runs here.

VENV_DIR ?= $(HOME)/.cache/bokydojo-venv

PYTHON ?= $(shell \
	if [ -x .venv/bin/python ] && .venv/bin/python -c '' >/dev/null 2>&1; then \
		echo .venv/bin/python; \
	elif [ -x "$(VENV_DIR)/bin/python" ] && "$(VENV_DIR)/bin/python" -c '' >/dev/null 2>&1; then \
		echo "$(VENV_DIR)/bin/python"; \
	elif command -v python3 >/dev/null 2>&1; then echo python3; \
	else echo python; fi)

# -- Environment ---------------------------------------------------------------

# Report which interpreter the other targets will use, and whether it is usable.
# Run this first when a target fails with "not found" — it is almost always the
# wrong Python rather than a missing tool.
venv:
	@echo "Interpreter: $(PYTHON)"
	@$(PYTHON) -c 'import sys; print("Version:    ", sys.version.split()[0])'
	@$(PYTHON) -c 'import django; print("Django:     ", django.__version__)' 2>/dev/null \
		|| echo "Django:      NOT INSTALLED — run 'make install', or 'bash start.sh'"

install:
	$(PYTHON) -m pip install -e '.[dev]'

# -- Development ---------------------------------------------------------------

dev:
	$(PYTHON) manage.py runserver

# -- Testing -------------------------------------------------------------------

test:
	DJANGO_SETTINGS_MODULE=config.settings.test $(PYTHON) -m pytest --tb=short -q

# -- Linting & formatting ------------------------------------------------------

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

# -- Database ------------------------------------------------------------------

migrate:
	$(PYTHON) manage.py migrate

makemigrations:
	$(PYTHON) manage.py makemigrations

shell:
	$(PYTHON) manage.py shell

# -- Seed data -----------------------------------------------------------------

seed:
	$(PYTHON) manage.py seed

# -- Backup / restore (TODO 0.7.3 / §7.3) -------------------------------------

backup:
	$(PYTHON) manage.py backup

restore:
	$(PYTHON) manage.py restore

# -- Full check (run before committing) ----------------------------------------

check: lint test
	@echo "All checks passed."

# -- i18n — TODO 0.4.5 ---------------------------------------------------------

messages:
	$(PYTHON) manage.py makemessages --all

compilemessages:
	$(PYTHON) manage.py compilemessages
