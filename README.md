# BokyDojo

A Student Information System (SIS) for martial arts organisations: multi-dojo attendance, ranking, scheduling, billing, and a parent portal.

Self-hostable via Docker; managed single-tenant SaaS long term.

## Quick Start

```bash
# Clone and configure
git clone <repo-url> && cd BokyDojo
cp .env.example .env

# Start with Docker Compose (Postgres + Redis + app + worker + Caddy)
docker compose up -d

# Or run locally without Docker — creates the virtual environment, installs,
# migrates, seeds a demo, and serves it. Needs only Python 3.12+.
bash start.sh
```

Open [http://localhost:8000](http://localhost:8000) (or [https://localhost](https://localhost) with Docker Compose).
`start.sh` prints a working login for each role; the demo path is
`/login/` → `/today/` → mark a class → `/reports/attendance/`.

⚠ Use `bash start.sh` or the `make` targets rather than a bare `pip`/`python`.
Those names resolve only inside an activated virtual environment, and many Linux
distributions ship no `python` at all — only `python3`. See
[CONTRIBUTING.md](CONTRIBUTING.md#development-setup) for the by-hand setup and
the Windows/WSL caveats.

## Development

```bash
make venv      # Report which interpreter the targets use — start here if one fails
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
- [Contributing](CONTRIBUTING.md) — conventions and development guide

The threat model and control list (`security_and_compliance.md`), the adversarial
security review, and the competitor research are maintained outside this
repository. The review documents unremediated findings with working
reproductions, so it is not published alongside the code it describes; ask if you
need it for an audit.

## Run it with Podman or Docker

The image is published to GitHub Container Registry, so nothing here needs to be
cloned or built:

```sh
# 1. Fetch the two files that describe the stack
curl -fsSLO https://raw.githubusercontent.com/sarel-myburgh/BokyDojo/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/sarel-myburgh/BokyDojo/main/Caddyfile
curl -fsSLO https://raw.githubusercontent.com/sarel-myburgh/BokyDojo/main/scripts/init-env.sh
chmod +x init-env.sh

# 2. Generate real secrets (it refuses to overwrite an existing .env)
./init-env.sh

# 3. Up
podman-compose up -d        # or: docker compose up -d
```

Then open <https://localhost:8443/setup/> and use the first-run token that
`init-env.sh` printed. Caddy issues an internal certificate for `localhost`, so
your browser will warn once.

To pull the image on its own:

```sh
podman pull ghcr.io/sarel-myburgh/bokydojo:latest
```

Tags: `latest` tracks `main`, `sha-<commit>` pins an exact build, and `vX.Y.Z`
appears on releases. Built for `linux/amd64` and `linux/arm64`.

### Things worth knowing before you deploy it

- **Rootless Podman cannot bind ports below 1024**, so the stack publishes 8080
  and 8443 by default. A real deployment with a domain needs 80 and 443 for
  certificate issuance — set `BOKYDOJO_HTTP_PORT=80`, `BOKYDOJO_HTTPS_PORT=443`
  and either run rootful or lower `net.ipv4.ip_unprivileged_port_start`.
- **Set `DOMAIN` and `DJANGO_ALLOWED_HOSTS` to your hostname.** Django refuses
  requests for hosts it does not know, and Caddy will not get a certificate for
  a domain it has not been told about.
- ⚠ **`SMTP_HOST` must be real before you rely on the system.** The generated
  `.env` contains a placeholder so the stack boots for evaluation; password
  reset and every notification fail until it points at a transactional provider.
  A residential IP lands in spam — this is deliberate (plan §12.9), not an
  oversight.
- ⚠ **Back up `DJANGO_FIELD_ENCRYPTION_KEYS` somewhere other than the server.**
  It decrypts medical and safeguarding records. Lose it and those records are
  unreadable; there is no recovery path and there is not meant to be one.
- The web container publishes no port. It is reachable only through Caddy, which
  is what makes trusting `X-Forwarded-Proto` safe. Do not expose gunicorn
  directly.

To build from a checkout instead of pulling:

```sh
podman-compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```


## Licence

Copyright © 2026 Sarel Myburgh.

BokyDojo is free software under the **GNU Affero General Public License,
version 3 or later** — see [LICENSE](LICENSE). You may run it, study it, modify
it and self-host it. If you run a modified version as a network service, AGPL
§13 requires you to offer that version's source to its users.

**Commercial exception.** The copyright holder also grants proprietary licences
to anyone who wants to use BokyDojo without the AGPL's source-disclosure
obligations — embedding it in a closed product, or reselling it as a hosted
service. Enquiries: jmsarel@gmail.com.

Choosing AGPL was decision `D7` (project_plan.md §10). The reasoning: the
business is managed hosting and support, not code secrecy, so the source can be
open — but a competitor should not be able to take it and run a rival hosted
BokyDojo without contributing back.
