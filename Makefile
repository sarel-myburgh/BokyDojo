# =============================================================================
# BokyDojo — Makefile
# TODO 0.1.7 — dev, test, lint, migrate, seed, backup, restore
# =============================================================================

.PHONY: dev test lint format migrate seed backup restore shell check venv install bandit audit bump

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

# ⚠ The same secret scan CI runs — tracked files against the reviewed baseline.
# It was missing from `check` for three commits, CI was red for three commits,
# and the local gates passed the whole time. A check that only exists in CI is a
# check you find out about afterwards.
# ⚠ Both of these are CI's and were previously invisible locally, which is how a
# mark_safe call reached CI having passed `make check`. Every gate CI runs must
# be runnable here, or "green locally" means nothing.
# ⚠ Run before every push. The patch number goes up each time so the badge in
# the running app can be compared against what was deployed.
bump:
	$(PYTHON) scripts/bump-version.py

bandit:
	$(PYTHON) -m bandit -r apps/ -c pyproject.toml --severity-level medium

audit:
	$(PYTHON) -m pip_audit

secrets:
	# ⚠ --others as well as --cached. `git ls-files` alone lists only tracked
	# files, so a brand new file is invisible to this scan until after it has
	# been committed — which is precisely when it is too late, and is how a
	# test password reached CI having passed `make check` locally.
	git ls-files -z --cached --others --exclude-standard \
		| xargs -0 $(PYTHON) -m detect_secrets.pre_commit_hook --baseline .secrets.baseline

# Also CI's, and also previously invisible locally.
i18n-check:
	@matches="$$(grep -rlP '(?<=>)[A-Z][a-z]+(?:\s+[a-z]+)+(?=<)' templates --include='*.html' || true)"; \
	if [ -n "$$matches" ]; then \
		echo "ERROR: hardcoded English strings in templates; wrap them in {% translate %}:"; \
		echo "$$matches"; exit 1; \
	fi; \
	echo "No untranslated template strings found."

# ⚠ Mirrors every CI job except the container build, which needs Docker.
check: lint test secrets bandit audit i18n-check
	@echo "All checks passed."

# -- i18n — TODO 0.4.5 ---------------------------------------------------------

messages:
	$(PYTHON) manage.py makemessages --all

compilemessages:
	$(PYTHON) manage.py compilemessages
