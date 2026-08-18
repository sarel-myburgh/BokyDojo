# Contributing to BokyDojo

## After the rename to BokyDojo

⚠ The virtualenv path moved to `~/.cache/bokydojo-venv`. A venv cannot simply be
renamed — its console scripts hardcode the absolute path — so run `make venv` to
create the new one, then delete `~/.cache/dojomaster-venv`. `make venv` prints
which interpreter the targets resolved to, which is the first thing to check when
a target fails.

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

The short version — this creates the virtual environment, installs everything,
migrates, seeds a demo and serves it:

```bash
cp .env.example .env
bash start.sh
```

The long version, if you would rather do it by hand:

```bash
cp .env.example .env

python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

make migrate
make seed
make dev
```

Requires Python 3.12+ and, only if you intend to change a template, Node 18+.

⚠ **Do not use a bare `pip`, `python`, `pytest` or `ruff`.** They resolve only
inside an *activated* environment, and on many Linux distributions `python` does
not exist at all — only `python3`. Every `make` target runs tools as
`$(PYTHON) -m <tool>` for exactly this reason. If a target dies with
`pytest: not found`, run `make venv` first: it prints which interpreter is being
used and whether Django is importable, and the answer is almost always a wrong
Python rather than a missing tool.

⚠ **`python3 -m venv` fails on Debian and Ubuntu without `python3-venv`**, which
packages `ensurepip` separately. `start.sh` handles this by building the
environment with `--without-pip` and bootstrapping pip into it; by hand, either
install the distribution package or do the same.

⚠ **A `.venv` created on Windows cannot run on Linux.** Its layout is
`Scripts/python.exe`, and on an NTFS or DrvFs mount every file reads as
executable — so naive `-x` checks select a Windows binary and everything
afterwards fails confusingly. `start.sh` and the `Makefile` both *execute* a
candidate interpreter to prove it runs before trusting it, and fall back to an
environment outside the tree (`$HOME/.cache/bokydojo-venv`, overridable with
`BOKYDOJO_VENV`). The same applies under WSL, where the environment must live
outside `/mnt/c`.

### Front end

There is no JavaScript build step, but the stylesheet is compiled:

```bash
npm install
npm run build:css   # after ANY template change
npm run test:js
```

⚠ **Run `npm run build:css` after touching any template**, or newly used utility
classes are simply absent from the compiled stylesheet and the page renders
unstyled in places. `static/css/tailwind.css` is committed on purpose: the CSP
forbids a runtime CDN, so a checkout with no Node installed still has to render.

⚠ On a checkout made on Windows, `node_modules/.bin/tailwindcss` may arrive
without its executable bit and the build fails with `Permission denied`. Fix
with `chmod +x node_modules/.bin/tailwindcss`.

## Running Checks

```bash
make check    # lint + test — run this before committing
make lint     # ruff check + format check
make test     # pytest
make format   # auto-fix linting and formatting
make venv     # report the interpreter these targets will use
```

The full gate, matching what the suite is expected to pass:

```bash
make check
python manage.py makemigrations --check --dry-run --settings config.settings.test
npm run test:js
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

## Licensing of contributions ⚠

BokyDojo is AGPL-3.0-or-later, and the copyright holder additionally sells
commercial exceptions (see the README). That second half only works while one
party owns all the copyright: an exception cannot be granted over code somebody
else owns.

So **contributions are accepted only with a copyright assignment or a licence
grant broad enough to cover relicensing** — a signed CLA, or a `Signed-off-by:`
line plus explicit agreement that the copyright holder may also license the
contribution commercially. If you are not prepared to grant that, open an issue
describing the change rather than a pull request; do not send a patch that
cannot be accepted.

Nothing here applies to work done as commissioned or contracted development,
where copyright is assigned by the contract.

## Commit Messages

Use imperative mood. Reference the TODO task ID where applicable:

```
Add .env.example with all environment variables (0.1.3)
```
