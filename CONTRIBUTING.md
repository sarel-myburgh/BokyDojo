# Contributing to DojoMaster

## Non-Negotiable Conventions

These apply to every task, including delegated ones. Violating these is the most likely way a multi-agent handoff produces a broken codebase.

- **Tenancy** — every model carries `organization` (directly or via an unambiguous FK chain). No exceptions. `§7.2`
- **Scoped access** — all queries go through a scoped manager requiring an actor context. The unscoped manager is private (`_unscoped`) and its use outside migrations/admin commands fails lint. `SEC §2.2`
- **Opaque IDs** — UUIDv7 primary keys; never expose sequential integers in URLs or API responses. `SEC §2.2`
- **Money** — integer minor units + explicit currency code. Never floats. Never a bare number. `§6`
- **Strings** — every user-facing string is translatable (`gettext_lazy` in models, `{% translate %}` in templates). No hardcoded English. `§13.4`
- **Dates/times** — store UTC, render in the dojo's timezone. Recurrence uses RFC 5545 rrule. Beware DST when materialising sessions. `§4.5`
- **Audit** — every state-changing action writes an `AuditLog` entry with actor, before, after. `SEC §2.6`
- **Permissions** — every view has an object-level permission check *and* a test in the permission matrix suite. Menu-level hiding is not a control. `SEC §2.2`
- **Export** — every new model with user data is added to the full-export serialiser in the same PR that creates it. `§12.10`
- **Tests** — each task ships with tests. Attendance, auth, permissions and payments require them; nothing merges without.
- **Migrations** — one logical change per migration, reversible where possible, never edited after being pushed.
- **Mobile-first** — build the narrow viewport first, widen for desktop. Applies to admin too, not just the parent portal. `§12.15`
- **No secrets in the repo.** Ever. `SEC §2.4`

## Development Setup

```bash
# 1. Clone and set up environment
cp .env.example .env
# Edit .env with your settings

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Run migrations
make migrate

# 4. Start development server
make dev
```

## Running Checks

```bash
make check    # lint + test
make lint     # ruff check + format check
make test     # pytest
make format   # auto-fix linting and formatting
```

## Project Structure

```
apps/
  core/       — BaseModel, AuditLog, Money, scoping, managers
  identity/   — Organization, Dojo, Person, User, roles, permissions
config/
  settings/   — base.py / dev.py / prod.py / test.py
  celery.py   — Celery configuration
tests/         — pytest test suite
```

## [DS] Delegated Tasks

Tasks marked `[DS]` in TODO.md are self-contained and mechanical. They can be run by a cheaper model. See TODO.md for the delegation prompt template and rules.

## Commit Messages

Use imperative mood. Reference the TODO task ID where applicable:

```
Add .env.example with all environment variables (0.1.3)
```
