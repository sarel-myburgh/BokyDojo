# =============================================================================
# DojoMaster — Makefile
# TODO 0.1.7 — dev, test, lint, migrate, seed, backup, restore
# =============================================================================

.PHONY: dev test lint format migrate seed backup restore shell check

# -- Development ---------------------------------------------------------------

dev:
	python manage.py runserver

# -- Testing -------------------------------------------------------------------

test:
	DJANGO_SETTINGS_MODULE=config.settings.test pytest --tb=short -q

# -- Linting & formatting ------------------------------------------------------

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

# -- Database ------------------------------------------------------------------

migrate:
	python manage.py migrate

makemigrations:
	python manage.py makemigrations

shell:
	python manage.py shell

# -- Seed data -----------------------------------------------------------------

seed:
	python manage.py seed

# -- Backup / restore (TODO 0.7.3 / §7.3) -------------------------------------

backup:
	python manage.py backup

restore:
	python manage.py restore

# -- Full check (run before committing) ----------------------------------------

check: lint test
	@echo "All checks passed."

# -- i18n — TODO 0.4.5 ---------------------------------------------------------

messages:
	python manage.py makemessages --all

compilemessages:
	python manage.py compilemessages
