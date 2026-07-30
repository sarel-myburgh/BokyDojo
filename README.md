# DojoMaster

A Student Information System (SIS) for martial arts organisations: multi-dojo attendance, ranking, scheduling, billing, and a parent portal.

Self-hostable via Docker; managed single-tenant SaaS long term.

## Quick Start

```bash
# Clone and configure
git clone <repo-url> && cd DojoMaster
cp .env.example .env

# Start with Docker Compose (Postgres + Redis + app + worker + Caddy)
docker compose up -d

# Or run locally without Docker
pip install -e ".[dev]"
make migrate
make dev
```

Open [http://localhost:8000](http://localhost:8000) (or [https://localhost](https://localhost) with Docker Compose).

## Development

```bash
make dev       # Start dev server
make test      # Run tests
make lint      # Check linting
make format    # Auto-fix formatting
make check     # Lint + test
make migrate   # Run migrations
make seed      # Load seed data
```

## Architecture

- **Backend:** Django 5 + Django REST Framework
- **Database:** PostgreSQL 16
- **Frontend:** HTMX + Alpine.js + Tailwind (server-rendered)
- **Offline:** PWA with IndexedDB queue for attendance sync
- **Background jobs:** Celery + Redis
- **Deploy:** Docker Compose with Caddy reverse proxy

## Project Structure

```
apps/
  core/       — BaseModel, AuditLog, Money, scoping, managers
  identity/   — Organization, Dojo, Person, User, roles, permissions
config/
  settings/   — base.py / dev.py / prod.py / test.py
tests/        — pytest test suite
```

## Documentation

- [Project Plan](project_plan.md) — full feature spec and domain model
- [Security & Compliance](security_and_compliance.md) — threat model, controls, pentest scope
- [Competitive Analysis](competitive_analysis.md) — market research
- [Contributing](CONTRIBUTING.md) — conventions and development guide

## Licence

Copyright © 2026 Sarel Myburgh.

DojoMaster is free software under the **GNU Affero General Public License,
version 3 or later** — see [LICENSE](LICENSE). You may run it, study it, modify
it and self-host it. If you run a modified version as a network service, AGPL
§13 requires you to offer that version's source to its users.

**Commercial exception.** The copyright holder also grants proprietary licences
to anyone who wants to use DojoMaster without the AGPL's source-disclosure
obligations — embedding it in a closed product, or reselling it as a hosted
service. Enquiries: jmsarel@gmail.com.

Choosing AGPL was decision `D7` (project_plan.md §10). The reasoning: the
business is managed hosting and support, not code secrecy, so the source can be
open — but a competitor should not be able to take it and run a rival hosted
DojoMaster without contributing back.
