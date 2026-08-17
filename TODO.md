# DojoMaster — Build TODO

Working checklist for the whole project. Designed so **any agent can pick up mid-stream** without the original conversation.

**Read first:** [project_plan.md](project_plan.md) · `security_and_compliance.md` · `competitive_analysis.md`

> ⚠ The last two, and `security_review_2026-07-26.md`, are **deliberately not in
> this repository** — the remote is public and the review carries unremediated
> findings with working reproductions. They live beside your checkout and are
> listed in `.git/info/exclude` (local-only, so the filenames are not published
> in `.gitignore` either). `SEC §` references below still point at
> `security_and_compliance.md`; keep your copy to hand.

---

## How To Use This Document

**If you are an agent picking this up cold:**

1. Read the **Resume Point** block below — it says exactly where work stopped.
2. Read the referenced plan sections (`§`) for the task you're taking. Don't invent design; it's already decided.
3. Read **Non-Negotiable Conventions** (next section). These apply to *every* task and are the main way a handoff goes wrong.
4. Work top-to-bottom within a phase. Tasks are ordered by dependency.
5. Tick the box, and update the Resume Point block, when a task is **done and tested** — not when the code is written.

**Task sizing:** each unticked box is intended to be one focused work session or less. If a task turns out bigger, split it in place and leave the sub-boxes.

**Notation:**
- `§4.2` — section in project_plan.md
- `SEC §2.2` — section in security_and_compliance.md
- `⚠` — getting this wrong is expensive to undo; do not delegate, do not rush
- `[DS]` — **delegable to Deepseek / OpenCode** (see below)

### `[DS]` — delegable tasks

Tasks marked `[DS]` are self-contained and mechanical: standard framework boilerplate, simple models, straightforward CRUD screens, config files, report queries. They can be run in parallel by a cheaper model without needing the full architectural picture.

**A task qualifies as `[DS]` only if all of these hold:**
- No security, auth, permission, payment, or tenancy-scoping logic
- No architectural decision left open — the plan fully specifies it
- Small blast radius if implemented badly; easy to review and verify
- Doesn't require understanding more than one or two other modules

**Never delegate:** anything marked `⚠`, anything under Phase 4.5 (AI), anything under Phase 6 (security gate), and anything touching the permission resolver, offline sync, payment callbacks, or the governance-model split.

**Rules for delegated work:**
- The Non-Negotiable Conventions below still apply in full. State them in the delegation prompt.
- Delegated tasks still need tests and still need review before ticking.
- If a `[DS]` task turns out to need a design decision, stop and escalate rather than guessing. Note it in the Resume Point block.

**Capability note:** the `[DS]` line was originally drawn conservatively. MiniMax M2.5-class models (~80% SWE-bench Verified) handle this tier comfortably, so several tasks were promoted on 2026-07-26. The line is now drawn by **blast radius and reviewability**, not by difficulty — a subtle error in the permission resolver is a cross-tenant leak that no test you thought to write will catch, which is why ⚠ items stay in-house regardless of model capability.

### Approved models and budgets ⚠

Two CLIs are available, both authenticated. **Neither is metered in a way that
should make you ration it** — spend capacity on getting the work right rather
than conserving it.

#### opencode — default dispatcher

Runs in `Code/DojoMaster-opencode`. Writes files headlessly with **no permission
flags at all**, which is why it is the default.

**Only these two families are approved.** The picker lists 448 models; most are
pay-per-token OpenRouter routes and are *not* approved.

| Family | Count | Cost | Use |
|---|---|---|---|
| `opencode-go/*` | 16 | flat $10/month subscription, **no per-token cost** | The workhorse. |
| `opencode/*-free` | 7 | $0 | Smoke tests, throwaway checks, trivial tasks. |

The full Go-plan set (verify with `opencode models \| grep opencode-go`):

```
deepseek-v4-flash   deepseek-v4-pro   glm-5.1        glm-5.2
grok-4.5            hy3               kimi-k2.6      kimi-k2.7-code
kimi-k3             mimo-v2.5         mimo-v2.5-pro  minimax-m2.7
minimax-m3          qwen3.6-plus      qwen3.7-max    qwen3.7-plus
```

Free tier: `opencode/mimo-v2.5-free`, `laguna-s-2.1-free`, `ling-3.0-flash-free`,
`deepseek-v4-flash-free`, `nemotron-3-ultra-free`, `north-mini-code-free`,
`big-pickle`. These are limited-time beta offers — do not build the plan around
them.

⚠ **`opencode/*` without a `-free` suffix is pay-as-you-go Zen credit and is NOT
approved.** `opencode/glm-5.2` bills per token; `opencode-go/glm-5.2` is covered
by the subscription. One character apart, completely different bill.

Go has 5-hour / weekly / monthly usage caps rather than per-token charges, so a
very large batch can be throttled. That is the only reason to pace it.

```bash
# from Code/DojoMaster-opencode
opencode run "Read TASK_BRIEF.md in the repository root and carry out exactly what it specifies." \
  -m opencode-go/mimo-v2.5-pro
```

#### CommandCode — second dispatcher

Runs in `Code/DojoMaster-commandcode`. **$1 bought $100 of usage**, so it is
effectively free capacity — roughly 100× leverage. Use it freely; do not treat
the credit figures as a scarce budget.

Approved: `xiaomi/mimo-v2.5`, `xiaomi/mimo-v2.5-pro`, `deepseek/deepseek-v4-pro`,
`minimaxai/minimax-m3`, plus the free `poolside/laguna-s-2.1-free` and
`inclusionai/ling-3.0-flash-free`.

Only one cost note still matters, and only for very heavy use: **DeepSeek V4 Pro
carries a 4× usage multiplier and MiniMax M3 a 2×**, so they consume the balance
several times faster than MiMo for the same work. Not a reason to avoid them,
just not the first reach.

```bash
# from Code/DojoMaster-commandcode
commandcode -p "Read TASK_BRIEF.md in the repository root and carry out exactly what it specifies." \
  -m xiaomi/mimo-v2.5-pro \
  --max-turns 120 --skip-onboarding -t --yolo
```

#### Grok — fourth dispatcher

Runs in `Code/DojoMaster-grok`. Installed at `C:\Users\Sarel\.grok\bin\grok.exe`
(Windows PATH, added at install time — a shell opened earlier will not see it).
Logged in via grok.com.

⚠ **Only `grok-4.5` is available on this account.** Composer 2.5 is not
present; `grok models` lists one model. Verify before assuming otherwise.

```bash
grok --cwd <worktree> --always-approve -m grok-4.5 \
  -p "Read TASK_BRIEF.md in the repository root and carry out exactly what it specifies."
```

`--always-approve` is the equivalent of CommandCode's `--yolo` and carries the
same caveats. `-p/--single` is the headless flag. Suited to large self-contained
features rather than review.

#### Codex — high-stakes second opinion

Runs in `Code/DojoMaster-codex`. Installed and available with **GPT Tera** and
**GPT Sol**. **The highest-value agent so far**: one adversarial review found
18 issues including a critical cross-tenant privilege escalation, every one with
a working reproduction. See `security_review_2026-07-26.md`.

Note it has a dedicated `codex review` subcommand as well as `codex exec`, and a
`--sandbox` policy (`read-only` / `workspace-write`) rather than an all-or-
nothing bypass.

⚠ **Do not pipe `codex exec` through `Select-Object -Last N`** — it truncates
the report and discards findings. Have it write to a file instead.

Reserved for work where being wrong is expensive, not for volume:

- Adversarial review of ⚠ work before it merges — the permission resolver, the
  `same_organization_fields` guard, payment callback verification, the AI tool
  catalogue.
- The Phase 6 multi-model security review (`6.11`), where it is one of the
  named reviewers alongside GLM 5.2 and Kimi K3.
- A second opinion when a cheaper model produced something that looked right
  but a review found defects — the pattern has now recurred three times.

Do **not** route routine `[DS]` volume here. The value is a different model
family looking at the same code, and that is wasted on boilerplate.

#### Choosing between them

| Need | Use |
|---|---|
| Routine `[DS]` volume | **opencode** — no permission bypass required |
| opencode throttled, or a model only it has | **CommandCode** |
| Large self-contained feature | **Grok** (`grok-4.5`) |
| ⚠ review, security work, high-stakes correctness | **Codex** (Tera / Sol) |

Four agents can run at once, one per worktree. Before dispatching a parallel
batch, **pre-create any shared scaffolding yourself** (new app skeletons,
`INSTALLED_APPS` entries) so no two agents edit the same shared file. Doing that
once removed the whole class of collision from the four-way fan-out.

Prefer **opencode** by default — needing no permission bypass is a real security
difference, not a convenience one.

#### Operational notes (both CLIs)

⚠ **Put the brief in a `TASK_BRIEF.md` file, not in the command argument.** The
npm `.ps1` shim mangles multi-line string arguments — a long CommandCode prompt
fails with `too many arguments. Expected 1 argument but got 2`. Pointing at a
file also leaves a record of exactly what was asked.

⚠ **Review is not optional, and test count is not a quality signal.** Both
agents have shipped correct-looking work with real defects:
- CommandCode wrapped *stored rank names* in `gettext_lazy`, which would have
  made `get_or_create` miss its own rows under a different locale and silently
  duplicate an entire ladder.
- opencode wrote 16 tests for one model, of which two pairs were exact
  duplicates and two asserted a constant equals itself — while omitting the only
  test that mattered, cross-dojo isolation.

Read the diff. Check what is *missing*, not just what is present.

⚠ **CommandCode only: budget ~120 turns.** A four-model batch with tests hit the
60-turn cap mid-fix (exit code 8 means cap-hit, not failure). Work is resumable:
rewrite `TASK_BRIEF.md` with a continuation brief and dispatch again.

⚠ **CommandCode only: `--yolo` is required and is authorised.** It will not write
files or run shell commands in headless (`-p`) mode under any lesser permission —
`--auto-accept`, `-t --auto-accept`, `--permission-mode auto-accept` and
`--config permissions.defaultMode` were all tried and refused. The user
authorised `--yolo` on 2026-07-26 **for runs confined to an agent worktree**.

Conditions attached to that authorisation:
- Run only from the agent's own worktree, on its own branch.
- Everything else committed first, so repo damage is recoverable via git.
- Always cap `--max-turns`.
- Every diff reviewed before it reaches `main`. The agent does not commit.
- ⚠ Understand what git does *not* cover: the worktree isolates repository
  changes, but `--yolo` also permits arbitrary shell commands, which run as the
  user and can touch anything on the machine. That residual risk was accepted
  knowingly; do not describe the worktree as a sandbox.

**opencode needs none of this.** It writes files headlessly with no permission
flag, so the `--yolo` exposure above applies to CommandCode runs only. That is
the main reason opencode is the default.

- `--skip-onboarding` is required for CommandCode — taste onboarding blocks a
  headless run.
- Do **not** pass `-w/--worktree`; the agent already has one. Stacking them
  puts the work somewhere nobody is looking.
- `--output-format json` emits an NDJSON event stream — use it when you want a
  record of what the agent actually did, and to track token spend per run.

### Delegation prompt template

```
You are implementing one task from a specified project plan. Do not exceed its scope.

TASK: <id> — <text>
PLAN SECTIONS (read these first, they are authoritative): <§refs>
FILES YOU MAY TOUCH: <paths>

Before writing code, read:
  - TODO.md § "Non-Negotiable Conventions" — these apply in full, no exceptions
  - CONTRIBUTING.md
  - the plan sections listed above

RULES
  1. The design is already decided. Implement it; do not redesign it.
  2. If anything is ambiguous or the plan does not cover a case you hit, STOP and
     report the ambiguity. Do not guess and do not invent behaviour.
  3. Do not touch files outside the list above. If you believe you need to, stop and say so.
  4. Ship tests with the implementation. No tests, not done.
  5. Every user-facing string translatable. Every model org-scoped. Every mutation audit-logged.
     Money as integer minor units. UUIDv7 opaque IDs.
  6. Do not add dependencies without saying why in your output.

OUTPUT
  - Summary of what you changed, file by file
  - Anything you were unsure about (explicitly, even if you proceeded)
  - Anything you noticed that is out of scope but looks wrong
```

---

## Resume Point

> **Update this block whenever you stop work.**

- 📁 **Single folder, single branch.** The parallel worktrees have been
  consolidated: everything is merged into `main` in `Code/DojoMaster`, the five
  `agent/*` branches are deleted, and the `DojoMaster-*` directories are gone.
  Work directly here. The multi-agent section below is retained as a record of
  how to set the fan-out up again, not as a description of the current state.
- **Current phase:** Phase 0 complete / Phase 1 in progress
- ⚠ **This session — the app was serving no static files at all.**
  `STATICFILES_DIRS` was never set, and no app ships its own `static/` dir, so
  the finders never looked in the project-level `static/` tree. Every asset
  404ed: both stylesheets *and* every piece of offline-attendance JavaScript.
  The pages still rendered, so 981 tests stayed green while the UI was unstyled
  and the service worker's `cache.addAll` rejected on install — meaning the
  `1.6.x` offline queue was silently dead in a real browser, despite being
  ticked. Fixed in `config/settings/base.py`, with the regression test in
  `tests/test_pwa.py` (`test_every_shell_asset_actually_resolves`) asserting the
  worker's own shell list resolves. **Lesson: a green suite says nothing about
  asset delivery. Open the page.**
- **Also this session — the service worker could never ship an update.**
  `/static/` was cache-first with no revalidation, and Django serves unhashed
  filenames, so an installed PWA was pinned to whatever CSS and JS it first saw:
  a fix to `roster.js` could not reach an instructor's phone unless someone
  remembered to bump `CACHE`. Now stale-while-revalidate. The test that forbade
  this asserted `"cache.put" not in source` — right intent, wrong mechanism; it
  now checks *where* the writes happen instead.
- **UI reworked to a deliberate design language.** Warm ink-and-cotton palette
  (`gray` is overridden in `tailwind.config.cjs`, so all 28 templates retheme
  from one place), near-square corners, one crimson accent used sparingly, and a
  four-part vocabulary in `static/css/tailwind-input.css`: `.obi` (belt rule),
  `.eyebrow` (tracked label), `.wordmark`, `.seal`. ⚠ **Templates are styled via
  the `gray-*` stops on purpose — retheme there, don't add a parallel palette.**
  ⚠ **Run `npm run build:css` after touching any template**, or new utility
  classes are missing from the compiled build.
- ⚠ **`0.4.4` is ticked but its font claim is false.** No webfont is bundled
  anywhere; `dojo.css` used to say the Khmer face loaded "via Google Fonts in
  base.html", which it did not and could not — the CSP sets `font-src 'self'`.
  The stack now relies on OS-provided faces (fine on Android/iOS/Windows,
  unverified elsewhere). Bundling a subset woff2 is still outstanding.
- **Demo logins are short now:** `admin@karate.test`, `dojoadmin@karate.test`,
  `instructor@karate.test`, `parent@karate.test`, numbered from the second
  holder onwards. `.test` is RFC 6761 reserved so a misconfigured demo cannot
  mail a real person; change `DEMO_EMAIL_DOMAIN` in the seed command if you want
  a different one. Students still get generated addresses — most never log in.
- **Last completed:** offline attendance `1.6.1`–`1.6.5`: installable PWA shell,
  IndexedDB queue, CSRF-protected and tenant-scoped sync endpoint, idempotent
  replay, optimistic conflict detection, visible pending/conflict state, and a
  20-student network-off/reconnect JavaScript test. Canonical attendance writers
  lock the session row so concurrent reconnects cannot bypass stale-write checks.
- **Also completed:** `1.1.2` encrypted medical fields plus a strict medical
  access service. Sensitive values never enter generic audit snapshots or the
  generic admin; reads and writes require medical permissions and are access-logged.
- **Consent evidence completed:** `1.1.6` is append-only and exact-versioned;
  revocations supersede rather than mutate grants. Signer capacity, custodial
  guardian status, explicit self-consent age, signature, IP, document linkage,
  tenant integrity, permission-checked reads, and audit evidence are enforced.
  Dedicated `1.1.7` medical and `1.1.8` waiver screens now publish separate,
  immutable organisation-authored policy versions, capture deliberate guardian
  or self decisions, and serve attached waivers through permission-checked,
  audited downloads. Demo seed wording is conspicuously marked for replacement.

- **Student directory completed:** `1.1.9` adds tenant-scoped name/contact, dojo,
  rank, status, age, attendance-gap, unsigned-waiver, and expired-licence filters,
  plus permission-gated consent actions. Federated organisation actors cannot
  search or render dojo-private contact and birth-date-derived fields.
- **Saved segments completed:** `1.1.10` persists validated personal filter sets,
  reapplies them through the same scoped form, and provides audited create/delete
  controls. Search values stay out of audit snapshots; CSRF, ownership, tenant,
  malformed-ID, duplicate, and schema-tampering cases fail closed.
- **Student detail hub completed:** `1.1.11` adds the permission-scoped header,
  pinned operational alerts, and attendance, rank-history, notes, billing,
  documents, and family tabs. Billing is honestly marked as a Phase 2 placeholder.
  Medical alerts use a narrow audited flag read; sensitive documents, notes,
  family contacts, and federated private fields are filtered server-side.
- **Student lifecycle completed:** `1.1.12` adds a row-locked, permission-checked
  transition service and mobile detail-page control for join, trial, hold, resume,
  lapse, alumni, and return paths. Invalid jumps and tampered choices fail closed;
  audit failure rolls back the change. Hold reasons are tenant-encrypted, existing
  values are backfilled by a reversible migration, and values never enter audits.
- **Bulk hold/resume completed:** `1.1.13` adds mobile directory selection and
  a 50-student, tenant-scoped batch action. Each batch is atomic, accepts only
  active students for hold or held students for resume, reuses strict per-student
  audits, rejects tampered IDs, and rolls everything back on any invalid state or
  audit failure.
- **Student photos completed:** `1.1.14` adds a separate exact-version photo
  consent screen, permission-checked upload/display routes, byte-sniffed image
  validation, EXIF/GPS-stripping re-encoding, private no-store rendering, and
  latest-photo selection without deleting history. Revocation immediately blocks
  profile display and direct document reads; federation-level actors cannot read
  dojo-owned photographs. Demo wording is clearly marked for replacement.
- **Guardian management completed:** `1.1.4` adds audited add/edit/unlink screens,
  multiple independently flagged contacts per student, and reuse of one guardian
  Person across siblings. Guardian notes are encrypted and excluded from audits
  and generic admin; dojo scoping now covers guardian links and emergency contacts,
  and shared contact details require edit rights over every linked child.
- **Manual promotion completed:** `1.2.6` records forward-only internal awards
  from the student rank tab, with tenant and dojo permissions, chronology checks,
  row locking, derived-current-rank updates, CSRF protection, and strict audit
  rollback. Rank awards are now append-only in models, querysets, and generic admin.
- **Bulk promotion and ceilings completed:** `1.2.7`/`1.2.8` reuse the
  canonical promotion service for atomic batches of up to 30 students. Examiner
  ceilings filter choices and are rechecked server-side, including self-promotion
  and cross-ladder attempts; one invalid student or audit failure rolls back the batch.
- **Just completed:** `1.5.6` attendance catch-up, plus `0.6.2`/`0.6.3`
  mandatory TOTP MFA and one-time recovery codes with encrypted seeds, hashed
  recovery values, replay prevention, step-up enforcement, and throttling.
- **Also completed:** `0.6.6` single-use password reset and `0.6.7`
  strict nonce CSP with all runtime assets self-hosted and no inline executable content.
- **Security audit:** dependency CVE scan clean; Bandit medium/high clean; Django
  deployment checks clean. Demo seed credentials are disabled outside dev/test,
  and the holiday API transport is pinned to HTTPS on the expected host.
- **Backup/restore completed:** PostgreSQL custom dump plus hashed media archive;
  restore requires exact database confirmation and rejects traversal, links,
  tampering, and broad media roots. Real client binaries are present, but a live
  restore was not run because no local PostgreSQL credentials were available.
- **Phase 0 complete:** the first-run wizard transactionally creates the
  organisation, first dojo, owner, mandatory admin role, and then closes forever.
  Production requires a deployment setup token. Automated UI/template tests pass;
  a browser visual pass remains for the human dry run because the in-app browser
  could not reach the WSL loopback server and no Chrome session was connected.

- 🚀 **The app is clickable end to end.** `python manage.py runserver
  --settings config.settings.dev` after `manage.py migrate && manage.py seed
  --clear`; the seed prints a working login per role. The path is
  `/login/` → `/today/` → `/sessions/<id>/roster/` → `/reports/attendance/`.
  Mark a class from a phone-width viewport — that is the product's core loop and
  it works.

- ⚠ **Two bugs found only by opening the pages, not by tests:** times rendered
  in UTC (an 18:30 class showed as 11:30), and multi-line `{# #}` template
  comments rendered as visible text. Both now have regression tests. **Look at
  the screen** before ticking a UI task; a green suite proved nothing about
  either.

- ✅ **Review debt on `1.1.x` is cleared.** Reading those tests surfaced a bug
  none of them covered — see `same_organization_fields` below.

- ⚠ **Timezones:** never format a datetime without deciding whose timezone it is
  in. `apps/core/timezones.py` has the only sanctioned resolvers — `dojo_zone()`
  in Python, `{% timezone session.local_zone %}` in templates (pass the resolved
  tzinfo, never the raw name — the tag raises on a bad one). Middleware activates
  the actor's own zone, which also decides where `__date` report boundaries fall.

- **Attendance is written through one service.** `apps.attendance.services.
  mark_attendance` — idempotent on `client_generated_id`, applies the visiting
  flag, enforces the retroactive-edit permission. The kiosk (`1.7`) and the
  offline sync endpoint (`1.6.3`) must call it rather than writing rows, or the
  three paths will drift. Admin add is disabled for the same reason.

- ⚠ **New invariant every model author must know:**
  `TenantScopedModel.same_organization_fields`. Scoping decides who may *read*
  a row; it does not stop a row being *created* that points at two different
  organisations. Any model reached through an indirect tenant path must declare
  it, e.g. `same_organization_fields = ("person", "home_dojo")` — first name is
  the reference, the rest must match it. Enforced in `save()`, not just
  `full_clean()`. `tests/test_cross_org_integrity.py` fails if a new model
  references more than one organisation-bearing record without declaring it.

- **Landed since the consolidation:**
  - **Holidays reworked** (`1.4.4`). A `Holiday` is now a catalogue entry that
    creates **no** closure; a per-dojo `HolidayObservance` decides `closed` /
    `open` / `reduced_schedule` and manages the linked `ClosurePeriod`. Some
    dojos deliberately teach on holidays. Import via `NagerDateProvider` (free
    public API), `CsvProvider`, or a builtin of Cambodian **fixed-date** holidays
    only — lunar dates are never computed. Network access is an injected
    `fetch` callable; the suite makes no network calls.
  - **Per-organisation exchange rates** (`apps/core/exchange.py`). Every
    business in Cambodia quotes its own USD/KHR rate — 4000:1 here, 4100:1 down
    the road, both correct. Rates are org-scoped, effective-dated, and **there
    is no default**: `convert()` raises `NoExchangeRate` rather than guessing.
    Changing a rate adds a row so historic invoices keep converting at the rate
    that applied. An explicit reverse rate beats the derived inverse.
  - **Any ISO currency accepted.** Unlisted codes default to two decimals; only
    exceptions are named (KHR/VND/JPY zero, KWD/BHD three).
  - **Permission matrix is now independent** of `ROLE_ACTIONS`, fixing Codex's
    own finding. Expectations are literal, written from the plan. It agrees with
    the implementation everywhere.

- **Test suite:** 1236 passing, 52 skipped; Ruff lint and format gates clean;
  `makemigrations --check` clean; `npm run test:js` clean. Bandit medium/high and
  Django production deploy checks are clean. From WSL:
  `$HOME/.cache/dojomaster-venv/bin/pytest`. On Windows:
  `.venv/Scripts/python.exe -m pytest`.
- 🐧 **Running on native Linux now, not WSL/Windows — and the tooling assumed
  otherwise.** Every documented command was broken on a plain Linux box:
  `make test` died with `pytest: not found`, `make lint` with `ruff: No such
  file`, and `bash start.sh` selected the **Windows** `.venv/Scripts/python.exe`
  and then advised running a `.exe` to fix it. The suite was green the whole
  time, which is why nobody noticed. All fixed:
  - `start.sh` creates the environment on *any* platform, not only under WSL,
    and proves a candidate interpreter by **executing** it. ⚠ A Windows checkout
    marks every file executable on an NTFS/DrvFs mount, so `[[ -x ]]` cheerfully
    accepts a Windows binary on Linux.
  - It recovers when `python3 -m venv` fails for want of `ensurepip` (Debian and
    Ubuntu package it separately, and it bites hardest on a box with no sudo to
    fix it) by building `--without-pip` and bootstrapping pip.
  - The `Makefile` resolves an interpreter the same way and runs every tool as
    `$(PYTHON) -m <tool>`. **`make venv` prints which Python the targets use** —
    start there when one fails, it is almost always the wrong interpreter rather
    than a missing tool.
  - `README.md` and `CONTRIBUTING.md` now describe this instead of a bare
    `pip install`.
  Django resolves to **5.2.17 on Python 3.14** — one version and one interpreter
  ahead of what the rest of this file assumes, and the whole suite passes on it.
  ⚠ `node_modules/.bin/tailwindcss` arrived from the Windows checkout without an
  exec bit, so `npm run build:css` failed with "Permission denied" until
  `chmod +x`. If the CSS build dies on a fresh clone, check the mode bits first.
- ✅ **`0.4.4` is now true.** Its font half had been ticked while nothing was
  bundled. Noto Sans Khmer (OFL 1.1) is self-hosted at
  `static/fonts/noto-sans-khmer-khmer.woff2` with the `@font-face` in
  `dojo.css`. Decisions worth keeping:
  - **Khmer subset only, not Latin.** The family sits *after* the Latin faces in
    the `tailwind.config.cjs` stack, so it is never asked to render Latin; its
    Latin glyphs would be ~30KB nobody downloads for a reason. The declared
    family name matches the one already in that stack, so no config change was
    needed — the `@font-face` simply makes the existing entry real.
  - **`unicode-range` keeps it free for English.** The browser fetches the file
    only when a page actually contains Khmer. That is also why the font is
    deliberately **not** in the service worker's `SHELL` precache: adding it
    would force all 59KB onto every install. `/static/` is stale-while-
    revalidate, so it caches for offline use the first time a Khmer page is
    opened — which is exactly when it is wanted.
  - **One variable file** covers 100–900, so `font-weight` is a range.
  - `font-display: swap`, so an instructor on a bad connection sees the roster
    in a fallback face rather than blank space.
  - `static/fonts/OFL.txt` must stay next to the font; the OFL requires the
    licence to travel with it.
  Verified served: `200 font/woff2`, 58792 bytes, and the CSP's `font-src
  'self'` accepts it. `tests/test_fonts.py` (7 tests) parses the real
  `@font-face` rather than trusting the comment above it, and is checked against
  four broken variants — including a `src` pointing at a CDN, which is precisely
  the bug `0.4.4` originally shipped.
  ⚠ Still unverified by a human: what Khmer actually *looks* like in tables and
  buttons at narrow widths. The face now loads; nobody has eyeballed the
  wrapping.
- **Human dry-run debt:** install the PWA on the intended phone/tablet, mark a
  real 20-person roster with networking disabled, reconnect, and time the full
  instructor interaction. The automated 20-mark queue/reconnect and endpoint
  checks pass, but the human `1.6.6` under-30-second target is intentionally not
  ticked from a test-client timing.
- **`1.11.2` is done.** The view, template and CSV export were already on disk
  but the report was **unreachable** — no route in `config/urls.py`, no link
  from the other two reports. Now routed at `/reports/ranks/` as
  `active-by-rank`, cross-linked both ways, and covered by
  `tests/test_rank_report.py` (11 tests: grouping, ungraded, non-active
  exclusion, dojo scoping both directions, ordering, CSV scope, export audit,
  permission refusal, anonymous redirect).
- ⚠ **Two bugs the tests could not have found, both caught by opening the page.**
  1. **The seed never put a single student on a rank ladder.** It created the
     ladders (`1.2.11`) and then stopped: 140 profiles, 60 ranks, **zero**
     `StudentStyleTrack` and zero `RankAward` rows. Every rank surface in the
     demo — this report, the student rank tab, the promotion screens — showed
     one meaningless "no active rank track" bucket. `seed.py` now has
     `_create_rank_tracks`: ladder chosen by age (under 14 → mon), a grading
     roughly every four months, and ~1 in 6 deliberately left ungraded because
     that is a real state the screens must render. Reseeds to 140 tracks /
     458 awards.
  2. **Groups were sorted alphabetically by rank name.** The view computed
     `rank_order` and then threw it away. Kyu grades survive this by accident,
     but the mon ladder inverts: `10th Mon` — the most junior grade there is —
     sorted above `9th Mon`. Now ordered by `Rank.order`, seniors first,
     ungraded last. ⚠ The first version of the regression test passed against
     *both* sorts, because the adult grades I picked happened to sort the same
     either way; it only bites now because it uses the mon ladder. **Check that
     an ordering test fails against the old ordering before trusting it.**
- **`1.8.2` is done.** Visibility used to be honoured in exactly one place — an
  inline `Q` in `_visible_student_notes` — so the next screen to read a note
  would have had to reimplement the rules or quietly go without. It now lives on
  the queryset as `Note.objects...visible_to(actor, subject=..., governance_model=...)`
  and that helper delegates to it. ⚠ **Read notes through `visible_to`.**
  `for_actor` answers "which organisation's notes", which is a different
  question from "which of them may this person read"; scoping alone hands back
  every level, private notes included. `subject` is the record that carries the
  dojo (usually the `StudentProfile`) — a Note has an organisation but no dojo,
  so passing a Note to `can()` denies every dojo-scoped role.
  `parent_visible` now genuinely reaches a guardian of *that* child through
  `GuardianLink`; a guardian of another child gets nothing. There is still no
  parent-facing surface — `3.2` has to build one before a parent can use it.
  16 tests in `tests/test_note_visibility.py`, mutation-checked against three
  broken variants (no enforcement, guardian branch ignoring which child, admin
  folded into the instructor grant).
- ⚠ **The seed had the same hole for notes that it had for ranks:** zero `Note`
  rows, so the notes tab, the pinned header alerts (`1.8.3`) and the whole
  visibility filter were invisible to anyone clicking through. `_create_notes`
  now writes across all four levels, several notes per student **by different
  instructors**, because one note per student cannot demonstrate the rules —
  the demo has to let you sign in as two people and get two answers about the
  same child. Verified on seeded data: the private note's author (a plain
  instructor) sees `instructors` + their own `private` and not `admins`; the
  dojo admin sees `admins` + `instructors` and not that private note.
  **Lesson, twice over now: when a feature is ticked, check the seed exercises
  it.** A demo that renders an empty tab teaches the reader it is missing.
- **`1.8.4` is done.** SEC §4 asks for three things and each is enforced in a
  different place, deliberately:
  - **Encrypted.** `Note.body` is now an `EncryptedTextField`. ⚠ Encryption is a
    property of the *column*, not of a row, so **every** note body is encrypted,
    not only safeguarding ones — the stricter reading, and the only one the
    field type can express. Consequence: note bodies can never be searched. A
    note search, if ever wanted, must exclude safeguarding notes by construction
    rather than by filter. Migration `core/0008` encrypts existing rows in place
    and is reversible.
  - **Restricted to a named role.** `Note.Visibility.SAFEGUARDING`, reachable
    only through `apps.core.safeguarding.view_safeguarding_notes`.
    ⚠ `visible_to` excludes it **unconditionally, last** — so no clause can
    grant it, not an admin's and *not the author's own*. Whoever wrote it may
    since have left the role; §4 says "restricted to a named role", not "to a
    role or whoever typed it". A new screen therefore cannot leak one by
    forgetting a filter; it has to go and ask deliberately.
  - **Access-logged.** The service is eager, not a queryset, precisely so "log
    every access" is implementable — a lazy queryset may be evaluated never,
    once or repeatedly. Logged even when the result is empty, because someone
    going looking is the event. The log is `strict` and inside the transaction,
    so a failed write rolls back rather than serving an unlogged read.
    `actor_label` is written explicitly: `actor_person` is `SET_NULL`, and this
    is the log read years later about staff who have left.
  - `body` joins `audit.SENSITIVE_FIELDS`, and `Note.__str__` refuses to preview
    a safeguarding body — it is what admin changelists and tracebacks print.
  - The student detail page shows the section to the officer only, and fetches
    it **only when the notes tab is actually open**, or every page view would
    log an access and bury the signal.
  - 30 tests, checked against six broken variants (encryption dropped,
    `visible_to` exclusion removed, access log removed, `body` back in audit
    snapshots, and the backfill no-opped in each direction).
  - ⚠ Verified on seeded data, not just in tests: the officer sees the note, the
    org admin and the instructor see neither it nor the section's heading, and
    those three page visits produced exactly **one** access-log entry.
- ⚠ **Third seed gap in a row** — the seed had no `Role.SAFEGUARDING` holder and
  no safeguarding notes, so the control would have looked like a feature that
  does nothing. There is now one officer per dojo (`safeguarding@karate.test` /
  `safeguarding123!`) and a couple of notes per dojo. **When a task is ticked,
  check the seed exercises it**; that is now three features found dark this way.
- **Notes can now be written** (not on the plan; added because `1.8.x` shipped
  four visibility levels and no way to author one — every note came from the
  seed or the admin). Composer on the student notes tab, service in
  `apps/core/note_authoring.py`.
  ⚠ **The rule: you may only write at a level you could read back**, plus
  `private`, plus `safeguarding` if you hold the named role. The alternative —
  an instructor writing a note only admins can read — was rejected deliberately:
  a note you cannot read is one you cannot check or correct, and a write-only
  channel into a child's file is the shape of thing this system exists to
  prevent. **The cost is that an instructor cannot privately escalate to the
  office**; they write at `instructors`, which every admin can already read. If
  that trade is revisited, `writable_visibilities` and its docstring are what
  change. Per role: instructor gets 3 levels, dojo/org admin 4, the safeguarding
  officer all 5.
  The form builds its choices per actor, and `create_note` re-checks the level,
  because a posted field is client-supplied data. Audited as `create` recording
  the *level*, never the text.
  28 tests, checked against three broken variants. ⚠ One test name overclaimed:
  posting a level the form never offered is caught by the `ChoiceField` *before*
  the service, so that test stays green if the service re-check is deleted. It
  is renamed to say so, and the re-check is covered directly elsewhere.
  Verified in the running app: instructor offered 3 levels, officer 5, and an
  instructor posting `safeguarding` saved nothing.
- ⚠ **Encrypted bodies are not searchable, and this bit immediately.** A check
  script used `Note.objects.filter(body=...)` and silently matched nothing —
  every row has its own nonce, so identical plaintext gives different
  ciphertext. It is not a bug to work around: decrypt in Python, or do not
  encrypt. Any future note search must exclude safeguarding notes by
  construction.
- **`1.4.5` is done** — the "classic calendar-app trap" the plan warns about.
  `apps/scheduling/edits.py`, service-level only: scheduling still has no UI at
  all, and gets one with `1.4.9`.
  - **This occurrence** moves one session and records the slot it vacated in the
    new `ClassSession.moved_from`. ⚠ Without that the move silently becomes a
    *duplicate*: materialisation keys on `(template, starts_at)` and never
    deletes, so the next nightly run finds 18:30 standing empty and helpfully
    recreates the class. The generator now treats a slot as occupied if a session
    starts there **or was moved from there**. Recorded once and never
    overwritten, so moving twice still protects the generator's original slot.
  - **This and future** splits the template: the original is closed the day
    before, a successor starts on the day of, and past sessions keep pointing at
    the rule that actually produced them. Refuses a split from today or earlier —
    today may already have been taught, so that is a per-occurrence edit.
  - ⚠ **Only untouched future sessions are regenerated.** Cancelled, attended or
    individually-moved sessions are records of something somebody did: they keep
    their own time, are handed to the successor, and claim their date's new slot.
    Visible consequence: a date you had already cancelled keeps the cancelled
    class at its *old* time. The cancellation surviving matters more.
  - ⚠ **The bug I shipped and then caught.** The first version deleted the
    successor's duplicate *after* materialising. That looks correct and is not —
    the slot is empty again by the next nightly run, which refills it. Preserved
    rows must be re-pointed at the successor *before* it materialises, because
    `existing` is computed per template. Found only because the duplicate test
    was strengthened to re-run the generator; the same test passed happily
    against the broken version when it checked only the state right after the
    split. **A scheduling test that does not re-run materialisation proves very
    little.**
  - ⚠ Re-materialising a **stale in-memory template** regenerates the old rule —
    its `active_to` is still `None` in Python after the split. The real job loads
    templates fresh. This cost me a false failure; if a test sees ghost sessions,
    re-read the template first.
  - 23 tests, checked against five broken variants.
- ⚠ **`full_clean()` on a tenant-scoped model raises `UnscopedAccessError`.**
  `validate_unique` and `validate_constraints` issue their own queries through
  the scoped manager, which refuses to run without an actor. Pass
  `validate_unique=False, validate_constraints=False` for field validation only;
  the cross-tenant check that matters runs in `save()`. Same trap for reverse
  relations — `session.attendance_records` is scoped, so it cannot be read in a
  loop; gather the ids in one `for_actor` query instead.
- **`1.4.8` is done, and it was not `[DS]`-sized.** Nothing recorded who taught a
  class at all: plan §4.5 specifies `ClassTemplate.default_instructor_ids[]` and
  `ClassSession.instructor_ids[]`, and neither existed. That silently blocked
  `1.4.9` ("filtered by instructor" had nothing to filter on) and `1.9.3`
  (auto-drafting a timesheet needs somebody to pay).
  - `TemplateInstructor` is who *normally* teaches; `SessionInstructor` is who
    taught **one** class. ⚠ Pay and any safeguarding question about who was in
    the room read the second, never the first.
  - Materialisation seeds new sessions from the template defaults and ⚠ **only
    new ones** — re-seeding existing sessions would silently undo every
    substitution ever recorded.
  - A substitution is *recorded*, not overwritten: the stand-in is flagged and
    points at the person covered for, because "Dara covered for Mei on the 14th"
    is the fact a parent actually asks for. The template is untouched, so next
    week reverts on its own with nobody having to remember.
  - ⚠ A substitute may come from **another dojo in the same organisation** —
    that is what a substitute usually is. Requiring an assignment at *this* dojo
    would refuse the ordinary case.
  - ⚠ Unlike moving a class, assignment **is** allowed on a past session. "Who
    actually taught last Tuesday" is a correction of fact and pay depends on it;
    refusing would leave the record permanently wrong. Audited instead.
  - 17 tests, checked against three broken variants.
- ⚠⚠ **Fourth seed gap, and this one had teeth.** `InstructorAssignment` (`1.3.5`,
  ticked) had **zero rows in the entire demo** — the model existed and nothing
  ever wrote to it. So the "is this person actually an instructor?" check refused
  *every* substitution in the demo. The seed now creates one per instructor and
  head instructor. **Four features in a row have been found dark this way: ranks,
  notes, safeguarding, and now teaching. Assume a ticked feature is unexercised
  by the seed until checked.**
- ⚠ **Django multi-join gotcha, cost me a false failure.** `.filter(rel__x=...)`
  combined with `.exclude(rel__y=...)` on the *same* reverse relation turns the
  join into a LEFT JOIN, so rows with **no** related records satisfy the
  `__isnull=True` filter and slip through. Walk the related model directly
  instead of excluding across a reverse relation.
- **`1.4.9` is done — scheduling has a UI at last.** `/calendar/`, week and
  month, per dojo, filtered by instructor. `apps/scheduling/calendars.py` holds
  the data layer; the view is thin and read-only on purpose.
  - ⚠ **The grid is bucketed by each dojo's own local date, never the
    viewer's.** A 19:30 class in Phnom Penh is on Tuesday for an org admin
    reading from London, where that instant is Tuesday lunchtime, *and* from
    Auckland, where it is already Wednesday. The consequence to accept: on a
    multi-dojo page two classes in one cell did not necessarily happen at the
    same time, and the column header is not a single instant. "What happens at
    each dojo on its own calendar" is the question actually being asked, and a
    timetable that disagrees with the one on the dojo wall is worse than none.
  - ⚠ **A calendar test written with the viewer and the dojo in the same
    timezone passes against every implementation, right or wrong.** The
    bucketing tests need an org whose `default_timezone` differs from the
    dojo's, or they prove nothing. Mine were each run against a broken variant
    first — and the first draft of the week-boundary test **survived** deleting
    the local-date filter entirely, because the bucketing loop dropped the
    session anyway. It was rewritten into two tests that each kill their
    mutation. **Check a calendar test fails against UTC bucketing before
    trusting it.**
  - ⚠ `_OFFSET_MARGIN` pads the SQL window by a day either side. A 06:00 Monday
    class in Phnom Penh happened at 23:00 the *previous* Sunday in UTC, before
    the unpadded window opens; without the margin every week silently starts
    late for every dojo east of Greenwich. The exact date test then runs in
    Python, per row, because SQL cannot apply a per-dojo zone name.
  - ⚠ Instructors are gathered in one `for_actor` query and hung on the session
    objects as `.teaching`. `session.session_instructors` is a **scoped reverse
    relation** and raises `UnscopedAccessError` the moment a template loops it.
  - Filters that name a record (`dojo`, `instructor`) **404** when they are out
    of scope; the `date` filter is tolerant and falls back to today. Silently
    dropping a bad dojo id would *widen* the page to every dojo the actor can
    see, which is the opposite of what was asked for.
  - Gated on `DOJO_VIEW`, not `ATTENDANCE_VIEW` — this is what the dojo has on,
    not attendance data, and it is the one action every staff role holds,
    including the safeguarding officer.
  - 31 tests in `tests/test_calendar_views.py`, checked against seven broken
    variants (viewer-zone bucketing, UTC bucketing, no window padding, no
    local-date filter, naive `+30 days` month stepping, dropped closure
    narrowing, ignored instructor filter).
- ⚠⚠ **Fifth seed gap, and this one hid a whole feature: zero `ClosurePeriod`,
  zero `Holiday`, zero `HolidayObservance` rows in the entire demo.** All of
  `1.4.4` — the holiday rework whose entire point is that some dojos teach on a
  holiday — was invisible, and the calendar's closure display would have looked
  like dead code. `seed.py` now has `_create_closures`, called **before**
  materialisation so the generator genuinely skips those dates (it reports
  `6 skipped for closures`). Two holidays per org, the second observed
  `closed` at one dojo and `open` at another, plus one ad-hoc non-holiday
  closure, because otherwise every closure on screen arrives through the
  holiday table. **That is five features found dark this way — ranks, notes,
  safeguarding, teaching, and now closures. Assume a ticked feature is
  unexercised by the seed until checked.**
- **`HolidayObservance.apply()` now writes the holiday's name as the closure
  `reason`, not `"Closed for {name}"`.** Found by opening the page: it rendered
  as "Closed — Closed for Khmer New Year". `reason` is a label and every screen
  showing one has already said the dojo is shut; ad-hoc closures are nouns
  ("Floor resurfacing") and this matches them. One line, no migration, covered
  by `test_the_closure_reason_is_the_holidays_name`.
- **`1.4.10` is done — Phase 1 scheduling (`1.4`) is now complete.** The field
  existed with a migration and nothing else: no vocabulary, no validation, no
  way to read it back, and one test asserting it defaults to `[]`.
  `apps/scheduling/class_types.py` is the sanctioned entry point.
  - **The vocabulary is per organisation, in the settings hierarchy**
    (`scheduling.class_type_tags`), not an enum in code. `kata`/`kihon`/`kumite`
    are karate words; a BJJ club replaces the list wholesale. ⚠ **Organisation
    scope only, deliberately** — if a dojo could override it, the rule "≥10
    kata" would mean different things at two dojos in one organisation and a
    transferring student would gain or lose progress with no record of why.
  - ⚠ **Tags are refused, never normalised.** A template tagged `Kata` against a
    rule naming `kata` matches **nothing**, silently, and surfaces months later
    as a student wrongly held back from a grading. Coercing the case would make
    them agree by luck while hiding that somebody has two names for one thing.
    A case variant is rejected with a message naming the tag that does exist,
    so the author sees a typo rather than a system fault.
  - ⚠ **`JSONField.__contains` is a Postgres-only trap and was the whole design
    fork.** Tests and dev run on **SQLite**, production on **PostgreSQL**;
    `filter(counts_toward__contains=[tag])` raises
    `NotSupportedError: contains lookup is not supported on this database
    backend`. Verified, not assumed. `icontains` against the serialised JSON is
    worse — a substring match, so `kata` finds `kata_advanced`. Templates are
    filtered **in Python** and sessions by `template_id__in`, which is portable
    and cheap because templates are inherently few (one per weekly slot per
    dojo). If a deployment ever grows enough templates for that to hurt, the fix
    is a join table, **not** a JSON lookup.
  - ⚠ **A one-off session counts toward nothing.** `ClassSession.template` is
    null for ad-hoc classes and the tags live on the template, so an ad-hoc kata
    seminar contributes to no eligibility rule. Recorded, not fixed — `3.6.2`
    must either exclude them by design or give `ClassSession` its own override.
  - ⚠ **Changing the vocabulary does not retag existing templates.** Removing a
    tag that is in use leaves those rows untouched and readable, but the next
    save of that template fails validation. Nothing currently prevents the
    removal. Worth a guard before an admin UI for the vocabulary exists.
  - `SettingDefinition` gained an optional `validator` hook, because `choices`
    asks "is the value one of these", which is the wrong question for a
    list-valued setting — without it the vocabulary had no validation at all.
    It also runs against the declared `default` in `__post_init__`, so a broken
    default fails at import rather than on first read.
  - Validation is enforced in `save()`, like `check_same_organization` and for
    the same reason: the seed, fixtures and every service write skip
    `full_clean()` — which a tenant-scoped model cannot call anyway.
  - 26 tests in `tests/test_class_types.py`, checked against five broken
    variants (case silently normalised, `save()` no longer validating,
    substring tag matching, vocabulary allowed at dojo scope, declared default
    never validated).
- **The seed now tags its timetable**, differently per class — Little Dragons
  `kihon`, Juniors `kihon`+`kata`, Adults four tags, Saturday
  `kata`+`grading_preparation`. A demo where every class counts toward
  everything cannot demonstrate "of which at least 10 kata"; the counts now come
  out meaningfully uneven (446 kata, 507 kihon, 66 grading_preparation).
- **Import has an engine — `1.10.2`, `1.10.3` done; `1.10.1`/`1.10.7` are UI
  debt only.** New app `apps/imports/`. Chosen before the kiosk because import
  is on the critical path to the pilot (`1.12.1` is impossible without it) while
  the kiosk sits behind `D1`, which is still an *assumption*, and duplicates a
  capture path that already works.
  - **`ImportedRecord` maps source key → created row**, in its own table rather
    than an `external_id` column on Person and everything else. Import is not a
    property of a student; it is a property of how that student arrived. Five
    importers share one mechanism and the domain models stay clean.
  - ⚠ **A dry run takes the same code path as a real one** and rolls back. A
    separate "validate" pass is a dry run that lies: it drifts with every change
    and cannot see the failures that only appear on write — a unique constraint,
    a `save()` refusing a value, a second row in the same file colliding with
    the first. The tests assert against the **database afterwards**, not the
    reported counts, because "12 created" while having actually created them is
    the failure worth catching.
  - ⚠ **The `ImportRun` is written outside the rolled-back transaction**, or a
    dry run rolls back the very report the operator is meant to read.
  - ⚠ **Each row runs in its own savepoint, and the suite cannot prove it.**
    On PostgreSQL a failed statement poisons the transaction and every later row
    fails; SQLite tolerates it. Tests run on SQLite, production on PostgreSQL —
    verified by deleting the savepoint and watching
    `test_one_bad_row_does_not_stop_the_others` **pass anyway**. Guarded by a
    source-structure assertion instead, which is weaker and is the strongest
    thing available here. Running the suite against PostgreSQL (`5.2.6`) is the
    real fix, and that test should then be replaced by a behavioural one.
  - ⚠ **Blank cells never erase.** A partial re-import — a corrected phone
    column only — must not wipe every address it did not carry.
  - ⚠ **Identity is the whole problem.** With `external_id` mapped the key is
    reliable; without it the key is name + date of birth, which is a heuristic
    that collapses two real students sharing both. Case-folded (not lowered, so
    ß and dotted-I fold correctly) so a re-export with different capitalisation
    is not a new student. Guardians key on email where present, which is what
    makes two siblings share one parent Person instead of two.
  - ⚠ **`%m/%d/%Y` is deliberately not accepted.** `03/04/2015` is ambiguous and
    guessing turns a March birthday into an April one silently; the error names
    the problem and tells the operator to use ISO.
  - ⚠ **A blank line mid-file defeats `csv.Sniffer`** — found by running a
    realistic export, not by testing. It sniffs any *prefix* of the file
    correctly and returns "Could not determine delimiter" for the whole sample,
    because a row with no delimiters fails its consistency check. The comma
    fallback then parsed a semicolon file as a **single column** and the import
    confidently reported four rows whose only field was the whole line. The
    sample now skips blank lines and the fallback counts candidates in the
    header. Excel produces those blank lines constantly.
  - ⚠ **CSV cannot be sniffed for type**, so `apps.core.uploads.validate_upload`
    (magic bytes, for images and PDFs) is deliberately not reused. Validation is
    by decoding and parsing: size cap, row cap, `utf-8-sig` then `cp1252`.
  - Gated on **both** `PERSON_CREATE` and `PERSON_EDIT`: a re-import updates, so
    create-only rights would otherwise overwrite people through the importer
    that the actor could not touch through the student screens.
  - `manage.py import_csv` acts **as a named person**, not as the system actor,
    and its permissions are checked rather than bypassed. Defaults to a dry run;
    writing needs `--commit`, spelled out.
  - 38 tests in `tests/test_imports.py`, checked against six broken variants
    (dry run writing for real, no idempotency lookup, blank cells erasing,
    guardian keyed per student, fallback key not case-folded, savepoint removed).
  - Verified end to end against seeded data with a deliberately nasty file (BOM,
    semicolons, a 31-February date, a blank line, siblings): first run 3 created
    / 1 errored, second run **0 created / 3 updated**, one guardian Person with
    two links.
- **Best next tasks in Phase 1**, in dependency order:
  - **The import web wizard** (`1.10.1` + `1.10.7`) — one screen serving both:
    upload → map columns → preview the dry run → commit → download the report.
    The engine underneath is done and tested.
  - Then `1.10.4` attendance and `1.10.5` rank history — both reference students,
    so they needed `1.10.3` first — and `1.10.6` competitor presets.
  - Then the `1.7.x` kiosk (12 tasks, none started). `D1` should be settled
    before starting it, since it is what decides whether the kiosk is wanted.
  - `D10` still blocks the `1.12` exit gate regardless of code. Scheduling still
    has **no editing UI** — `1.4.5`'s move/split and `1.4.8`'s assignment are
    service-level, and the calendar is deliberately read-only.
  - Notes are **create-only** — no edit, no delete, no soft-delete. That was a
    scope decision, not an oversight, but a typo in a pinned note currently
    cannot be fixed from the UI. Decide whether notes are append-only evidence
    (like rank awards) or ordinary editable records before someone assumes.
- ⚠ **Never edit TODO.md with PowerShell `Get-Content -Raw` / `Set-Content`.**
  PS 5.1 reads a BOM-less file as ANSI and rewrites it as UTF-8, mangling every
  em-dash and `§` in the document. Use an editor/Edit tool, or Python with
  explicit `encoding="utf-8"`.
- **New in this session:** `ScopedQuerySet.for_organization()` — the sanctioned
  actorless scoping entry point, for subject-driven reads with no logged-in user
  (kiosk, background jobs). Use it instead of `.unscoped()` whenever a tenant
  filter *is* being applied, just not from an actor.
- **Encryption is available:** `EncryptedTextField` / `EncryptedCharField` from
  `apps.core.fields`. Task `1.1.2` (medical fields) is unblocked.
  ⚠ Encrypted columns cannot be filtered, ordered or indexed — the field
  constructor refuses `db_index` and `unique` rather than failing silently.
- **Open questions blocking work:** none in Phase 0 or Phase 1. `D7` (licence) is
  **decided** — AGPL-3.0-or-later plus a commercial exception; `LICENSE` is in
  place. `D10` (pilot dojo) still blocks the Phase 1 exit gate `1.12`, and only
  that.
- ✅ **The Phase 1 backlog is committed.** For eleven days ~70 untracked paths
  held work that was *ticked as done* — all of MFA / password reset / CSP, the
  backups, the first-run wizard, medical fields, guardians, every consent flow,
  the whole student directory / detail / lifecycle / photos group, promotions,
  **the entire PWA in `static/js/`**, 11 migrations and ~20 test files. One
  `git clean` would have deleted most of Phase 1. It is now in `main` as twelve
  themed commits, `dcd7236`..`54573a1`, working tree clean.
  ⚠ Those commits are grouped for *readability*, not bisectability: shared files
  (`identity/models.py`, `config/urls.py`, the settings module) landed early and
  whole, so an individual commit in that range is not independently green. The
  tree as a whole is — do not `git bisect` across them expecting a build.
  **Do not let it drift again**: rule 1 of the workflow section is never leave
  work uncommitted.
  `/node_modules/` is in `.gitignore` so `git add -A` is safe;
  `static/css/tailwind.css` is intentionally *not* ignored, because the CSP
  forbids a CDN and a Node-less checkout still has to render.
- **Deviations from the plan so far:**
  - `Person` was made a `SoftDeleteModel` rather than a plain `TenantScopedModel`. Rationale: student records are attached to attendance, rank awards and invoices, all of which are evidence; erasure requests go through redaction, not DELETE. Consistent with plan §2 ("never hard-delete user data"). Migration `identity/0002`.
  - `0.3.6` (Money) and `0.3.1` (BaseModel) were marked `[DS]` but built in-house — everything else depends on them, so the parallel track would have blocked.

### Multi-agent workflow — *not currently active*

> ⚠ **This section is history, not instructions.** The worktrees and `agent/*`
> branches described below were removed on 2026-07-26 when everything was
> consolidated into a single `main` in `Code/DojoMaster`. Keep it as the recipe
> for setting the fan-out up again; do not follow it as a description of how the
> repository is laid out today.
>
> To restore it: recreate the branches, `git worktree add ../DojoMaster-<name>
> agent/<name>` per agent, and pre-create any shared scaffolding first.

Several agents work this repo. Running them in one shared working tree caused a
duplicate implementation (`0.3.4` written twice), two tasks ticked before they
were finished, and one test run against a half-written file. Each agent now has
its **own branch and its own directory** (a git worktree), all sharing one
`.git`. Agents no longer see each other's uncommitted work.

#### Worktrees — find your directory

| Directory | Branch | Agent |
|---|---|---|
| `Code/DojoMaster` | `main` | integration + inspection — **nobody commits here** |
| `Code/DojoMaster-claude` | `agent/claude` | Claude |
| `Code/DojoMaster-commandcode` | `agent/commandcode` | CommandCode |
| `Code/DojoMaster-opencode` | `agent/opencode` | OpenCode / MiniMax |
| `Code/DojoMaster-codex` | `agent/codex` | Codex |

**Work only in your own directory.** `cd` there and stay there. Your branch is
already checked out; you never need `git checkout`, and attempting to check out
another agent's branch will fail (git refuses a branch checked out elsewhere —
that refusal is the safety mechanism).

#### Environment inside a worktree

There is one shared virtualenv, in the primary directory. From any worktree:

```bash
../DojoMaster/.venv/Scripts/python.exe -m pytest        # Windows
../DojoMaster/.venv/bin/python -m pytest                # POSIX
```

- `.env` is gitignored, so a new worktree has none. Copy it: `cp ../DojoMaster/.env .env`
- `media/`, `staticfiles/` and `__pycache__` are per-worktree. That is fine.
- ⚠ **Do not run `docker compose up` in two worktrees at once** — they bind the
  same host ports and share the same volume names. One at a time, or set
  `COMPOSE_PROJECT_NAME` and override the ports per worktree.

#### Managing worktrees

```bash
git worktree list                                  # show all
git worktree add ../DojoMaster-foo agent/foo       # add one
git worktree remove ../DojoMaster-foo              # remove (must be clean)
git worktree prune                                 # tidy stale entries
```

Removing a worktree does not delete the branch or any commits.

#### Rules

1. **Never leave work uncommitted at the end of a session.** It is invisible to
   everyone else and will be stranded if branches move. Commit, even if the
   message is "WIP".
2. **Claim before you build.** Add your task id to the Claimed list below and
   commit that change *first*. If a task is already claimed, take another.
3. **Tick only what is tested.** A model class existing is not the task done.
   "Model + write helper + middleware" means all three, plus tests, or the box
   stays empty. This has already gone wrong twice.
4. **Small, frequent merges to `main`.** Finish a task → tests green → ruff
   clean → merge to `main` → push. Do not sit on a branch for twenty tasks.
5. **Rebase on `main` before merging** so `main` stays linear and readable.
6. **Shared files are the collision risk.** `config/settings/base.py`,
   `pyproject.toml`, `TODO.md`. Make the smallest possible edit; never rewrite
   `TODO.md` wholesale.
7. **Migrations conflict badly.** If you add a model, merge to `main`
   immediately — two agents generating `0003_*` on separate branches is a manual
   fix. If it happens: delete one, re-run `makemigrations` after merging.
8. **Never `git push --force` a shared branch.**

#### Merging a finished task

Run all of this **from your own worktree**. `git checkout main` will not work —
`main` is checked out in the primary directory, and git will refuse.

```bash
# 1. commit your work
git add -A && git commit -m "..."

# 2. pick up anything main gained while you were working
git rebase main

# 3. prove it still works, post-rebase
../DojoMaster/.venv/Scripts/python.exe -m pytest
../DojoMaster/.venv/Scripts/python.exe -m ruff check .

# 4. fast-forward main without leaving your directory
git -C ../DojoMaster merge --ff-only agent/<you>
```

Step 3 matters: a rebase can produce a tree that compiles but fails, and nobody
checks after the fact.

If the rebase conflicts in `TODO.md`, keep **both** sides' ticks — you are each
completing different tasks, and neither tick is wrong.

Other agents pick up your work with `git rebase main` in their own worktree.

#### Claimed right now

| Task | Agent | Since |
|---|---|---|
| _(none)_ | | |

---

## Non-Negotiable Conventions

Apply to every task, including delegated ones. Violating these is the most likely way a multi-agent handoff produces a broken codebase.

- [ ] **Tenancy** — every model carries `organization` (directly or via an unambiguous FK chain). No exceptions. `§7.2`
- [ ] **No row spans two organisations** — if a model has more than one FK to an organisation-bearing record, declare `same_organization_fields`. Scoping controls reads; this controls what can exist. `SEC §2.2`
- [ ] **Scoped access** — all queries go through a scoped manager requiring an actor context. The unscoped manager is private (`_unscoped`) and its use outside migrations/admin commands fails lint. `SEC §2.2`
- [ ] **Opaque IDs** — UUIDv7 primary keys; never expose sequential integers in URLs or API responses. `SEC §2.2`
- [ ] **Money** — integer minor units + explicit currency code. Never floats. Never a bare number. `§6`
- [ ] **Strings** — every user-facing string is translatable (`gettext_lazy` in models, `{% translate %}` in templates). No hardcoded English. `§13.4`
- [ ] **Dates/times** — store UTC, render in the dojo's timezone. Recurrence uses RFC 5545 rrule. Beware DST when materialising sessions. `§4.5`
- [ ] **Audit** — every state-changing action writes an `AuditLog` entry with actor, before, after. `SEC §2.6`
- [ ] **Permissions** — every view has an object-level permission check *and* a test in the permission matrix suite. Menu-level hiding is not a control. `SEC §2.2`
- [ ] **Export** — every new model with user data is added to the full-export serialiser in the same PR that creates it. `§12.10`
- [ ] **Tests** — each task ships with tests. Attendance, auth, permissions and payments require them; nothing merges without.
- [ ] **Migrations** — one logical change per migration, reversible where possible, never edited after being pushed.
- [ ] **Mobile-first** — build the narrow viewport first, widen for desktop. Applies to admin too, not just the parent portal. `§12.15`
- [ ] **No secrets in the repo.** Ever. `SEC §2.4`

---

## Phase 0 — Foundations

**Goal:** deployable empty app with login, org/dojo/person admin, and a passing permission-matrix suite. `§8 Phase 0`

### 0.1 Repository & tooling
- [x] `0.1.1` `[DS]` Initialise git repo, `.gitignore`, `README.md` with a one-command dev setup *(needs `D7` licence decision for the LICENSE file)*
- [x] `0.1.2` `[DS]` Django project skeleton; settings split `base` / `dev` / `prod`, all config from env
- [x] `0.1.3` `[DS]` `.env.example` with every variable documented
- [x] `0.1.4` ⚠ Startup guard: refuse to boot on default `SECRET_KEY`, or `DEBUG=True` bound to a non-loopback interface `SEC §2.4`
- [x] `0.1.5` `[DS]` `pyproject.toml`: ruff + black + pytest config
- [x] `0.1.6` `[DS]` Pre-commit hooks (format, lint, secret scan)
- [x] `0.1.7` `[DS]` `Makefile` / `justfile`: `dev`, `test`, `lint`, `migrate`, `seed`, `backup`, `restore`
- [x] `0.1.8` `[DS]` `CONTRIBUTING.md` documenting the conventions above (so future agents read them)

### 0.2 Containers & CI
- [x] `0.2.1` `[DS]` Multi-stage `Dockerfile` (build deps separated, non-root runtime user)
- [x] `0.2.2` `[DS]` `docker-compose.yml`: `web`, `worker`, `db` (Postgres 16), `redis`, `caddy`
- [x] `0.2.3` `[DS]` Caddyfile with automatic HTTPS
- [x] `0.2.4` `[DS]` Health endpoint (`/healthz`) — db + redis + migration state
- [x] `0.2.5` `[DS]` Structured JSON logging to stdout
- [x] `0.2.6` `[DS]` CI: lint, test, build image
- [x] `0.2.7` `[DS]` CI: dependency audit (`pip-audit`), SAST (Semgrep/Bandit), secret scanning `SEC §7.4`
- [x] `0.2.8` `[DS]` Background job runner wired (Celery+Redis, or django-q2 if dropping Redis) `§7.1`

### 0.3 Core primitives
- [x] `0.3.1` `[DS]` `BaseModel`: UUIDv7 pk, `created_at`, `updated_at`, `created_by`
- [x] `0.3.2` Soft-delete mixin + manager — composes with the scoped manager, do after `0.3.3` `§2`
- [x] `0.3.3` ⚠ Scoped manager pattern + actor context (prefer explicit over thread-local) `SEC §2.2`
- [x] `0.3.4` Lint rule / test that fails on `_unscoped` use outside allowed paths
- [x] `0.3.5` `[DS]` `AuditLog` model + write helper + middleware capturing actor/IP/UA `SEC §2.6`
- [x] `0.3.6` `[DS]` `Money` value type — integer minor units + currency, arithmetic guarded against mixed currency
- [x] `0.3.7` `Setting` model + resolver for the hierarchy `org → dojo → class → session → student` `§13.2`
- [x] `0.3.8` ⚠ Field-level encryption helper (envelope, per-org data key, keys outside DB) `SEC §2.3`
- [x] `0.3.9` ⚠ `Document` model + validated upload (magic bytes, size cap, generated names, outside web root), permission-checked serving view, EXIF stripping, SVG rejected `SEC §2.3`

### 0.4 i18n scaffolding
- [x] `0.4.1` `[DS]` `LocaleMiddleware`, locale paths, `USE_I18N`
- [x] `0.4.2` `[DS]` Locale stubs: `en`, `km`, **`zh-Hans`** `§13.4`
- [x] `0.4.3` `[DS]` `Person.locale` field + per-request locale resolution from the logged-in person
- [x] `0.4.4` `[DS]` Khmer font bundled + `lang` attributes + line-break CSS; verify wrapping in tables and buttons `§13.4` *(font bundled and served; human check of wrapping still outstanding)*
- [x] `0.4.5` `[DS]` `make messages` / `make compilemessages` targets
- [x] `0.4.6` `[DS]` CI check: fail on untranslated user-facing strings in templates

### 0.5 Identity & org structure
- [x] `0.5.1` `[DS]` `Organization` model `§4.1`
- [x] `0.5.2` ⚠ `Organization.governance_model` ∈ `{central, federated}` `§13.1`
- [x] `0.5.3` `[DS]` `Dojo` model — address, timezone, currency, contact `§4.1`
- [x] `0.5.4` `[DS]` `Person` model — one row per human, no auth fields `§4.2`
- [x] `0.5.5` Custom `User` model with optional 1:1 to `Person` (most students never log in)
- [x] `0.5.6` `Role` + `RoleAssignment` (role, scope_type `org|dojo`, scope_id) — a Person may hold several `§3`
- [x] `0.5.7` ⚠ Permission resolver: `(actor, action, object) → allow/deny`, deny by default `SEC §2.2`
- [x] `0.5.8` ⚠ `visibility_policy` resolver honouring `governance_model` — one place, not scattered conditionals `§13.1`
- [x] `0.5.9` ⚠ **Permission matrix fixture**: every role × resource × governance model → expected result `SEC §2.2`
- [x] `0.5.10` ⚠ Generate the permission test suite from that fixture; wire into CI

### 0.6 Auth hardening
- [x] `0.6.1` `[DS]` Argon2id password hashing
- [x] `0.6.2` ⚠ TOTP 2FA, mandatory for org/dojo admin and any financial or PII-export role `SEC §2.1`
- [x] `0.6.3` Recovery codes (generate once, show once, hashed at rest)
- [x] `0.6.4` Session config: HttpOnly, Secure, SameSite=Lax, idle timeout, absolute cap, rotate on privilege change
- [x] `0.6.5` Rate limiting + progressive lockout: login, reset, PIN, API — `apps/core/throttle.py`. Wired into the login view (`apps/identity/views.py`); ⚠ the reset and kiosk-PIN policies still have no call site — wire on `0.6.6` / `1.7.8`.
- [x] `0.6.6` ⚠ Password reset: single-use, short-lived, no user enumeration (response *and* timing)
- [x] `0.6.7` Security headers + strict CSP with nonces, no `unsafe-inline` `SEC §2.4`

### 0.7 Developer experience
- [x] `0.7.1` `[DS]` Seed command: 2 orgs (one of each governance model), 3 dojos, 200 students, 2 years of attendance, ranks, invoices
- [x] `0.7.2` `[DS]` Demo reset command (idempotent, safe to cron)
- [x] `0.7.3` `backup` / `restore` management commands (pg_dump + media tarball) `§7.3`
- [x] `0.7.4` First-run wizard: create org, first dojo, admin user, choose governance model

---

## Phase 1 — MVP: Run One Real Dojo

**Goal:** the pilot dojo abandons their spreadsheet. `§8 Phase 1`
**Do not start Phase 2 until a real dojo is using this.**

### 1.1 Students & families
- [x] `1.1.1` `[DS]` `StudentProfile` — status, home dojo, sizes, licence `§4.2`
- [x] `1.1.2` ⚠ Medical fields (allergies, conditions, medications, doctor, `do_not_spar`) with field-level encryption `SEC §2.3`
- [x] `1.1.3` `[DS]` `GuardianLink` — relationship, contact / emergency / financial / custody flags, independent of each other `§4.2`
- [x] `1.1.4` Multiple guardians per student, each independently contactable `§2 item 26`
- [x] `1.1.5` `[DS]` `EmergencyContact` (Person link or plain name/phone)
- [x] `1.1.6` ⚠ `ConsentRecord` — versioned, granular, revocable, timestamped `§4.2`
- [x] `1.1.7` ⚠ Medical consent collected as its own deliberate act, not bundled into terms `SEC §6.5`
- [x] `1.1.8` Waiver flow: present versioned document, capture signature + IP + timestamp
- [x] `1.1.9` `[DS]` Student list: filter by dojo, rank, status, age, attendance gap, unsigned waiver, expired licence `§2 item 25`
- [x] `1.1.10` `[DS]` Saved filter segments, reusable
- [x] `1.1.11` `[DS]` Student detail hub — header, pinned alerts, tabs (attendance / rank / notes / billing / documents / family)
- [x] `1.1.12` Student lifecycle status transitions: prospect → trial → active → on_hold → lapsed → alumni `§2 item 7`
- [x] `1.1.13` Bulk hold / resume (seasonal mass pauses) `§12.5`
- [x] `1.1.14` Student photo upload + re-encode + consent gate

### 1.2 Ranks
- [x] `1.2.1` `[DS]` `Style` model `§4.4`
- [x] `1.2.2` `[DS]` `RankLadder` (adult / junior variants) `§4.4`
- [x] `1.2.3` `[DS]` `Rank` — order, name, colour, stripes, min months, min classes, min age
- [x] `1.2.4` ⚠ `StudentStyleTrack` — rank is **per style**, not per student `§4.2`
- [x] `1.2.5` `[DS]` `RankAward` + derived `current_rank` (denormalised, recomputed on write)
- [x] `1.2.6` `[DS]` Manual promotion flow with audit
- [x] `1.2.7` `[DS]` **Bulk promotion** — 30 students in one action after a grading `§2 item 24`
- [x] `1.2.8` ⚠ `InstructorProfile.max_grading_rank_id` — grading ceiling enforced `§4.2`
- [x] `1.2.9` `[DS]` External / transfer-in rank recognition (`awarded_by_external_org`, recognised / provisional / not recognised) `§12.6`
- [x] `1.2.10` Negating award record for rank stripping (never delete) `§12.6`
- [x] `1.2.11` `[DS]` Seed a Shotokan adult ladder + a junior mon ladder as fixtures

### 1.3 Enrolment & transfers
- [x] `1.3.1` `Enrollment` — student ↔ dojo, primary flag, status, dates `§4.3`
- [x] `1.3.2` Multi-dojo enrolment (several active at once)
- [x] `1.3.3` ⚠ Transfer flow: end old enrolment, create new, write `TransferRecord`. Never mutate history. `§4.3`
- [x] `1.3.4` ⚠ Test: attendance/invoice history survives transfer intact
- [x] `1.3.5` `[DS]` `InstructorAssignment` — person ↔ dojo

### 1.4 Scheduling
- [x] `1.4.1` `[DS]` `ClassTemplate` — rrule, time, duration, room, capacity, rank/age bounds `§4.5`
- [x] `1.4.2` ⚠ `ClassSession` materialisation job, rolling 90-day horizon (mind DST)
- [x] `1.4.3` ⚠ `ClosurePeriod` (org/dojo, date range, reason, `suppress_billing`) consulted by the generator `§12.2`
- [x] `1.4.4` `[DS]` Seed public holidays for Cambodia; make the set per-country data
- [x] `1.4.5` ⚠ Template edit semantics: "this occurrence" vs "this and future" `§4.5` *(service; no UI until `1.4.9`)*
- [x] `1.4.6` `[DS]` Ad-hoc one-off sessions (no template)
- [x] `1.4.7` `[DS]` Cancel session + reason + notification hook
- [x] `1.4.8` `[DS]` Substitute instructor assignment *(service; no UI until `1.4.9`)*
- [x] `1.4.9` `[DS]` Calendar views: week/month, per dojo, filtered by instructor
- [x] `1.4.10` `ClassTemplate.counts_toward[]` tags — which class types count for which eligibility rules `§2 item 23`

### 1.5 Attendance — core
- [x] `1.5.1` ⚠ `AttendanceRecord` with `client_generated_id`, idempotent upsert `§4.5`
- [x] `1.5.2` `[DS]` Instructor roster UI — mobile, large targets, mark-all-present then deselect *(critical UX path: review against the 30-second target in `1.6.6` before ticking)*
- [x] `1.5.3` `[DS]` Status set: present / late / absent / excused / visiting
- [x] `1.5.4` `[DS]` Visiting-student attendance at a non-enrolled dojo
- [x] `1.5.5` Retroactive edit with audit trail
- [x] `1.5.6` ⚠ **Catch-up flow** — sessions in the last 14 days with no records, fast bulk entry, nag the instructor `§12.7`

### 1.6 Attendance — offline PWA
*Hardest correctness area in the project. None of this is delegable.*
- [x] `1.6.1` PWA shell: manifest, service worker, installable
- [x] `1.6.2` IndexedDB queue for pending attendance writes
- [x] `1.6.3` ⚠ Sync endpoint, idempotent on `client_generated_id`
- [x] `1.6.4` Conflict handling + visible sync state ("3 pending")
- [x] `1.6.5` ⚠ Test: full class marked with network disabled, syncs correctly on reconnect
- [ ] `1.6.6` Target: 20-student class marked in under 30 seconds `§11 risks`

### 1.7 Attendance — kiosk
- [ ] `1.7.1` ⚠ Device token auth (not a user session), per dojo, revocable, device list with last-seen `SEC §2.7`
- [ ] `1.7.2` ⚠ Roster scope: only sessions starting within ±X minutes, no other PII
- [ ] `1.7.3` `[DS]` `kiosk_display_mode` — `photo_grid` `§13.2`
- [ ] `1.7.4` `[DS]` `kiosk_display_mode` — `name_list` (searchable)
- [ ] `1.7.5` `[DS]` Instructor can switch mode live at the kiosk without an admin
- [ ] `1.7.6` `pin_policy` ∈ `off | optional | required`, resolved via the settings hierarchy
- [ ] `1.7.7` ⚠ Per-student PIN override; **stricter of class and student wins**
- [ ] `1.7.8` ⚠ PINs hashed, rate-limited, lockout, admin reset
- [ ] `1.7.9` No-consent students fall back to name entry automatically `§13.2`
- [ ] `1.7.10` `[DS]` Confirmation screen auto-returns to grid; no lingering roster
- [ ] `1.7.11` `[DS]` Printable QR student cards as a secondary check-in path `§12.8`
- [ ] `1.7.12` ⚠ Test: revoked device token is cut off immediately

### 1.8 Notes
- [x] `1.8.1` `[DS]` `Note` — polymorphic subject, visibility levels, pinned `§4.7`
- [x] `1.8.2` ⚠ Visibility enforcement: `private` / `instructors` / `admins` / `parent_visible`
- [x] `1.8.3` `[DS]` Pinned notes surface on the student header
- [x] `1.8.4` ⚠ Safeguarding notes restricted to a named role, encrypted, access-logged `SEC §4`

### 1.9 Instructor time (basic)
- [x] `1.9.1` `[DS]` `InstructorProfile` — pay type, rate, currency `§4.2`
- [x] `1.9.2` `[DS]` `TimeEntry` model + `pay_rate_snapshot` `§4.8`
- [ ] `1.9.3` `[DS]` Auto-draft entry when a session's attendance is completed
- [ ] `1.9.4` `[DS]` Weekly timesheet view for the instructor

### 1.10 Import (acquisition tooling)
- [ ] `1.10.1` `[DS]` Generic CSV importer: upload → column mapping → preview → dry run `§12.10` *(engine, mapping, preview and dry run done and usable via `manage.py import_csv`; the **web wizard** is what remains)*
- [x] `1.10.2` ⚠ Idempotent re-import (fix and re-run, don't duplicate)
- [x] `1.10.3` `[DS]` Import students + guardians
- [ ] `1.10.4` `[DS]` Import historical attendance
- [ ] `1.10.5` `[DS]` Import rank history
- [ ] `1.10.6` `[DS]` Named presets for common competitor export formats
- [ ] `1.10.7` `[DS]` Import report: created / updated / skipped / errored, downloadable *(report built and written to CSV by `--report`; **downloadable from the browser** waits on the wizard)*

### 1.11 Core reports
- [x] `1.11.1` `[DS]` Attendance summary by dojo / class / period
- [x] `1.11.2` `[DS]` Active students by rank
- [x] `1.11.3` `[DS]` Attendance drop-off alert list (no attendance in N days)
- [x] `1.11.4` `[DS]` CSV export on every report *(all three reports; exports are audited)*

### 1.12 Phase 1 exit
- [ ] `1.12.1` Pilot dojo's real data imported
- [ ] `1.12.2` Instructors trained; roster tested live for one full week
- [ ] `1.12.3` Feedback captured, blocking issues fixed
- [ ] `1.12.4` ✅ **Gate: pilot dojo has stopped using their spreadsheet**

---

## Phase 2 — Billing

**Goal:** the pilot dojo collects fees through the system. `§8 Phase 2`
**Model is invoice + reminder + parent pays. No auto-debit.** `§6`

### 2.1 Accounts & plans
- [ ] `2.1.1` `[DS]` `BillingAccount` — payer, currency, balance, dunning state `§4.9`
- [ ] `2.1.2` `[DS]` `BillingAccountMember` — siblings on one account
- [ ] `2.1.3` `[DS]` `Plan` — recurring / class pack / drop-in / one-off, dojo scope
- [ ] `2.1.4` `Subscription` as an **invoice-generation schedule**, not a payment mandate `§6`
- [ ] `2.1.5` Pause / resume subscription (injury, travel, holidays), billing suppressed while held
- [ ] `2.1.6` `[DS]` `Discount` — sibling, annual prepay, hardship, staff
- [ ] `2.1.7` ⚠ **Stackable discounts** with a family cap `§2 item 27`

### 2.2 Tax & invoices
- [ ] `2.2.1` ⚠ `TaxProfile` — country, rates by category, inclusive/exclusive, rounding, numbering scheme `§13.7`
- [ ] `2.2.2` `[DS]` Seed a Cambodia profile; verify GDT requirements with an accountant `§12.16`
- [ ] `2.2.3` `[DS]` `Invoice` + `InvoiceLine`, sequential numbering per scheme — ⚠ **numbering must be gapless and concurrency-safe** (`SELECT … FOR UPDATE` on a counter row, not `MAX()+1`); tax law requires no gaps. State this explicitly in the delegation prompt.
- [ ] `2.2.4` ⚠ Historic invoices snapshot the tax profile in force at issue
- [ ] `2.2.5` `[DS]` Invoice PDF, localised, org-branded
- [ ] `2.2.6` Proration on mid-period join
- [ ] `2.2.7` Void / credit note / write-off with reason codes (never delete)
- [ ] `2.2.8` ⚠ Multi-currency: KHR/USD, exchange-rate snapshot on the invoice `§6`

### 2.3 Payments
- [ ] `2.3.1` ⚠ `PaymentProvider` interface + capability flags `§13.7`
- [ ] `2.3.2` `Manual` provider — cash, bank transfer; **first-class, not a fallback** `§4.9`
- [ ] `2.3.3` `[DS]` Receipt generation + sequential receipt numbering
- [ ] `2.3.4` `[DS]` `PaymentAttempt` model storing raw request/response/callback
- [ ] `2.3.5` ABA PayWay: sandbox access obtained, throwaway spike completed `§11 risks`
- [ ] `2.3.6` ⚠ ABA PayWay: hosted checkout creation + hash signing
- [ ] `2.3.7` ⚠ ABA PayWay: server-side callback verification — signature first, then amount/currency/reference `SEC §2.5`
- [ ] `2.3.8` ⚠ Idempotent callback handling keyed on transaction ID; replays are no-ops
- [ ] `2.3.9` ⚠ Browser redirect never marks anything paid (test this explicitly)
- [ ] `2.3.10` Transaction status/check API for reconciliation
- [ ] `2.3.11` `[DS]` Daily reconciliation report — gateway vs recorded, mismatches highlighted `§6`
- [ ] `2.3.12` `[DS]` Alerts: amount mismatch, unexpected currency, duplicate txn, callback for unknown/paid invoice

### 2.4 Collections
- [ ] `2.4.1` ⚠ Dunning ladder: issue, due−3, due, +7, +14, escalate to dojo admin `§6`
- [ ] `2.4.2` `[DS]` Reminder templates, localised
- [ ] `2.4.3` `[DS]` AR ageing report
- [ ] `2.4.4` `[DS]` Account statement per billing account
- [ ] `2.4.5` Drop-in billing generated from attendance records

### 2.5 Phase 2 exit
- [ ] `2.5.1` One full billing cycle run against real data
- [ ] `2.5.2` Reconciliation clean for one month
- [ ] `2.5.3` ✅ **Gate: pilot dojo collects fees through the system**

---

## Phase 3 — Parents, Communications & Grading

`§8 Phase 3`

### 3.1 Notification infrastructure
- [ ] `3.1.1` ⚠ `NotificationChannel` provider interface + capability flags `§13.3`
- [ ] `3.1.2` ⚠ Email provider; mandatory external SMTP, SPF/DKIM validated at first run, refuse to proceed silently `§12.9`
- [ ] `3.1.3` `[DS]` Telegram bot provider + `/start` account linking flow
- [ ] `3.1.4` `[DS]` Per-recipient, per-category preferences (`billing`, `attendance`, `grading`, `schedule_change`, `announcement`, `instructor_comment`)
- [ ] `3.1.5` `[DS]` Template rendering per recipient locale
- [ ] `3.1.6` `[DS]` `MessageLog` — channel, template, status, cost, per recipient
- [ ] `3.1.7` `[DS]` Automatic fallback to email on preferred-channel failure
- [ ] `3.1.8` `[DS]` Announcements with audience selector (org / dojo / class / rank / individual)
- [ ] `3.1.9` ⚠ Rate limits and per-org send caps (prevent budget-burn abuse) `SEC §7.2`
- [ ] `3.1.10` ~~WhatsApp provider~~ — **deferred**, see `§13.3`. Revisit only when a client will fund the per-conversation cost and Meta verification.

### 3.2 Parent portal
- [ ] `3.2.1` `[DS]` Portal shell, separate URL namespace, mobile-first PWA `§5.8`
- [ ] `3.2.2` Guardian invite + account claim flow
- [ ] `3.2.3` ⚠ Multi-child view — one parent sees all their children
- [ ] `3.2.4` `[DS]` Child attendance history
- [ ] `3.2.5` `[DS]` Rank + progress to next grade
- [ ] `3.2.6` `[DS]` Upcoming schedule
- [ ] `3.2.7` Invoices + pay button
- [ ] `3.2.8` `[DS]` Self-service contact / medical detail updates (with admin notification)
- [ ] `3.2.9` Consent toggles, revocable, taking effect immediately
- [ ] `3.2.10` `[DS]` Language switcher
- [ ] `3.2.11` ⚠ IDOR test suite — parent A cannot reach parent B's child by any route `SEC §7.1`

### 3.3 Localisation delivery
- [ ] `3.3.1` `[DS]` Full `en` catalogue
- [ ] `3.3.2` `[DS]` Full `km` catalogue *(machine draft; native-speaker review required before launch)*
- [ ] `3.3.3` `[DS]` Full `zh-Hans` catalogue *(machine draft; native-speaker review required before launch)*
- [ ] `3.3.4` `[DS]` Localised date, currency, number, and name-order formatting `§13.4`
- [ ] `3.3.5` `[DS]` All notification templates translated
- [ ] `3.3.6` `[DS]` Documented process for adding a language without code changes

### 3.4 Instructor ↔ parent comments
- [ ] `3.4.1` `[DS]` `parent_visible` notes with optional notification `§13.5`
- [ ] `3.4.2` Immutable after 24h; edits leave a visible trail
- [ ] `3.4.3` `[DS]` Parent replies land in the instructor inbox
- [ ] `3.4.4` ⚠ Dojo admins can see all instructor↔parent threads — no private unlogged channel `SEC §4`
- [ ] `3.4.5` `[DS]` Template snippets for common comments

### 3.5 Curriculum & lesson plans
- [ ] `3.5.1` `[DS]` `CurriculumItem` — kata / kihon / kumite / terminology / theory `§4.4`
- [ ] `3.5.2` `[DS]` `RankRequirement` joining item ↔ rank
- [ ] `3.5.3` `[DS]` `LessonPlan` + `LessonPlanBlock` `§4.6`
- [ ] `3.5.4` `[DS]` Plan library with tags, rank targeting, duplicate-and-edit
- [ ] `3.5.5` `[DS]` Attach to session as an immutable snapshot
- [ ] `3.5.6` `[DS]` Printable / phone view
- [ ] `3.5.7` `[DS]` Per-student curriculum progress ("what's left for green belt") `§2 item 17`

### 3.6 Grading
- [ ] `3.6.1` `[DS]` `GradingEvent` — date, venue, examiners, fee `§4.4`
- [ ] `3.6.2` ⚠ Eligibility engine: min months, min classes, min age, **per-class-type weighting** `§2 item 23`
- [ ] `3.6.3` Manual eligibility override with reason + audit
- [ ] `3.6.4` `GradingRegistration` + fee invoicing
- [ ] `3.6.5` Score sheets, offline-capable on the day
- [ ] `3.6.6` Results → automatic rank award + certificate number
- [ ] `3.6.7` ⚠ Grading ceiling enforced — an examiner cannot award above their permitted rank `§4.2`
- [ ] `3.6.8` ⚠ Federated mode: org ratification step before award is final `§13.1`
- [ ] `3.6.9` `[DS]` Parent/student notification of results
- [ ] `3.6.10` `[DS]` Grading results report + pass rates

### 3.7 Instructor time (complete)
- [ ] `3.7.1` `[DS]` Submit → approve → reject workflow
- [ ] `3.7.2` `[DS]` Payroll period report + CSV export `§2 item 12`
- [ ] `3.7.3` `[DS]` Categories: class / admin / private lesson / event / travel

### 3.8 Public surfaces
- [ ] `3.8.1` `[DS]` iCal feed per dojo and per student `§2 item 22`
- [ ] `3.8.2` `[DS]` Public read-only timetable page per dojo
- [ ] `3.8.3` `[DS]` Embeddable signup / trial-booking form `§Positioning`

---

## Phase 4 — Shop, API & Completeness

`§8 Phase 4`

### 4.1 Public API
- [ ] `4.1.1` `[DS]` DRF setup + serialisers for core resources `§13.8`
- [ ] `4.1.2` `[DS]` OpenAPI 3 spec auto-generated + browsable docs page
- [ ] `4.1.3` ⚠ Scoped API tokens — per integration, per org/dojo, read/write per resource, expiring, revocable, last-used tracked
- [ ] `4.1.4` ⚠ Per-token rate limiting; every call audit-logged
- [ ] `4.1.5` `[DS]` Versioned routes (`/api/v1/`) + written deprecation policy
- [ ] `4.1.6` `[DS]` Webhook event definitions: `student.enrolled`, `attendance.recorded`, `invoice.paid`, `rank.awarded`, `grading.completed`
- [ ] `4.1.7` ⚠ HMAC-signed webhook payloads + retry with exponential backoff
- [ ] `4.1.8` `[DS]` Webhook delivery log visible to the client
- [ ] `4.1.9` ⚠ Bulk export endpoint as a **public, documented, advertised** API feature `§12.10`

### 4.2 Shop
- [ ] `4.2.1` `[DS]` `Product` + `ProductVariant` (size, colour, SKU, price) `§4.10`
- [ ] `4.2.2` `[DS]` `StockLevel` per dojo
- [ ] `4.2.3` `Order` + `OrderItem` → invoice lines on the existing billing account
- [ ] `4.2.4` `[DS]` Pickup-at-dojo default; no shipping engine
- [ ] `4.2.5` `[DS]` Backorder / pre-order (gi sizes are always out of stock)
- [ ] `4.2.6` `[DS]` Low-stock alerts
- [ ] `4.2.7` `[DS]` Shop in the parent portal

### 4.3 Completeness features
- [ ] `4.3.1` `[DS]` `Credential` tracking + expiry reminders + optional block on class assignment `§2 item 11`
- [ ] `4.3.2` Makeup credits: cancellation policy → token → expiry → redemption `§12.3`
- [ ] `4.3.3` Private lessons: 1:1 session type, own rate, instructor commission `§12.4`
- [ ] `4.3.4` `[DS]` Prospect / trial pipeline → conversion `§2 item 16`
- [ ] `4.3.5` `[DS]` Event pages — gradings, seminars, camps: register, pay, roster `§2 item 31`
- [ ] `4.3.6` `[DS]` Certificate PDF generation, org-branded
- [ ] `4.3.7` `[DS]` Federation affiliation: licence numbers, renewals, annual fees `§2 item 13`
- [ ] `4.3.8` `[DS]` Light expense tracking + accounting CSV export `§2 item 30`
- [ ] `4.3.9` `[DS]` Class fill-rate report by slot, dojo, instructor `§12.14`
- [ ] `4.3.10` `[DS]` Retention / churn report, **year-over-year** comparison `§12.5`
- [ ] `4.3.11` Bulk messaging, bulk invoicing, bulk waiver send `§2 item 24`
- [ ] `4.3.12` Class capacity + waitlist behind a per-dojo flag `§2 item 15`

---

## Phase 4.5 — Optional AI (BYOK)

**Gate: does not ship until the adversarial corpus passes.** `§13.6` · `SEC §5`
**Nothing in this phase is delegable.**

- [ ] `4.5.1` Provider config — base URL, model name, encrypted API key, per org. Off by default.
- [ ] `4.5.2` ⚠ Fixed read-only tool catalogue with enum/range-validated arguments `SEC §5.3`
- [ ] `4.5.3` ⚠ Server-side scope injection — `org_id` and dojo set from session, **never a model-supplied parameter**
- [ ] `4.5.4` ⚠ Verify no tool can mutate state (test, not assertion)
- [ ] `4.5.5` Aggregates by default; row-level PII behind a separate permission
- [ ] `4.5.6` Untrusted DB text fenced and delimiter-stripped (defence in depth only)
- [ ] `4.5.7` ⚠ Output sanitisation — no HTML, no auto-linking, **no remote image loading** (exfil path)
- [ ] `4.5.8` Translation assist — labelled, shown alongside original, never silently substituted
- [ ] `4.5.9` Analytics Q&A UI, admin/owner roles only, absent from the parent portal
- [ ] `4.5.10` Per-org spend cap, rate limit, kill switch
- [ ] `4.5.11` Full audit log: question → tools → arguments → results → answer
- [ ] `4.5.12` ⚠ Opt-in consent flow before any PII reaches a third-party LLM; provider added to sub-processor list `SEC §6`
- [ ] `4.5.13` ⚠ **Adversarial injection corpus** — injections in names, notes, dojo names, product descriptions `SEC §5.4`
- [ ] `4.5.14` ⚠ Corpus wired into CI; failing corpus blocks release

---

## Phase 5 — Managed Hosting

**This phase is the product, not polish.** `§13.10`

### 5.1 Provisioning
- [ ] `5.1.1` Provision script: VPS, DNS, TLS, compose stack, first-run config
- [ ] `5.1.2` ⚠ Per-client credentials — no shared SSH keys anywhere in the fleet `SEC §3`
- [ ] `5.1.3` ⚠ Short-lived certificate broker or bastion; no standing SSH
- [ ] `5.1.4` ⚠ Admin surfaces behind VPN/WireGuard, never open to the internet
- [ ] `5.1.5` ⚠ Per-client network isolation; no client-to-client reachability
- [ ] `5.1.6` Client offboarding: export → deliver → verifiably destroy VPS and backups

### 5.2 Operations
- [ ] `5.2.1` `[DS]` Centralised monitoring: uptime, cert expiry, disk, failed logins, version drift
- [ ] `5.2.2` ⚠ Log shipping off the client VPS (attacker can't delete evidence) `SEC §2.6`
- [ ] `5.2.3` Alerting: bulk export, mass record access, repeated authz failures, out-of-hours admin login, new token, backup failure
- [ ] `5.2.4` `[DS]` Automated OS security patching
- [ ] `5.2.5` ⚠ Staged upgrade pipeline: canary → cohort → fleet
- [ ] `5.2.6` Migration testing against restored production-shaped dumps `§12.11`
- [ ] `5.2.7` `[DS]` Fail2ban/CrowdSec + WAF or Cloudflare
- [ ] `5.2.8` ⚠ Admin access logging + break-glass procedure with alerting `SEC §6.6`

### 5.3 Backups
- [ ] `5.3.1` `[DS]` Scheduled backups on by default
- [ ] `5.3.2` `[DS]` Offsite target (S3 / Backblaze / rclone) configured at first run
- [ ] `5.3.3` ⚠ Encrypted at rest and in transit; keys stored separately `SEC §2.3`
- [ ] `5.3.4` ⚠ `restore --verify` — restores into a throwaway container and asserts row counts `§12.12`
- [ ] `5.3.5` ⚠ Automated restore verification on a schedule, monitored
- [ ] `5.3.6` `[DS]` Admin nag banner when the last verified backup is older than N days

### 5.4 Business & documentation
- [ ] `5.4.1` `[DS]` Public demo instance, seeded, reset nightly, no signup `§12.13`
- [ ] `5.4.2` `[DS]` Docs site: setup, admin guide, instructor guide, parent guide, API reference
- [ ] `5.4.3` `[DS]` Diagnostic command dumping versions/config/health for support tickets `§11 risks`
- [ ] `5.4.4` Billing for the hosting service itself
- [ ] `5.4.5` DPA template `SEC §6.4`
- [ ] `5.4.6` Published sub-processor list
- [ ] `5.4.7` TOMs document (security doc rewritten for non-technical readers — doubles as sales collateral)
- [ ] `5.4.8` Records of Processing (Art. 30)
- [ ] `5.4.9` Breach notification runbook
- [ ] `5.4.10` DPIA template for clients
- [ ] `5.4.11` `[DS]` Published security contact + disclosure policy `SEC §7.4`
- [ ] `5.4.12` ⚠ Trademark search on the product name before printing anything `§12.17`

---

## Phase 6 — Security Gate

Full plan in `SEC §7`. Nothing launches until this passes. **Nothing here is delegable.**

- [ ] `6.1` Threat model reviewed against the built system (systems drift from plans)
- [ ] `6.2` Automated baseline clean: CVEs, SAST, secrets, TLS, headers, image scan
- [ ] `6.3` Permission matrix suite complete and passing for **both** governance models
- [ ] `6.4` Authorisation testing: horizontal, vertical, cross-tenant, cross-governance
- [ ] `6.5` Manual application testing: IDOR, injection, XSS, CSRF, SSRF, upload, session
- [ ] `6.6` Payment testing: forged callbacks, replays, tampered amounts, currency confusion, concurrency races
- [ ] `6.7` AI injection testing — explicitly task testers with breaking tenant scope through the model
- [ ] `6.8` Infrastructure testing — ⚠ **from one compromised client VPS, can you reach another?**
- [ ] `6.9` Kiosk testing: stolen device, token replay, physical access
- [ ] `6.10` Business-logic cases from `SEC §7.2` all tested:
  - [ ] Backdate attendance to manufacture grading eligibility
  - [ ] Transfer dojos to escape an outstanding invoice
  - [ ] Self-approve own timesheet
  - [ ] Award yourself a rank above your grading ceiling
  - [ ] Order against someone else's billing account
  - [ ] Burn a client's notification budget
  - [ ] Drain a client's BYOK AI key
  - [ ] Enumerate students via shop or event endpoints
  - [ ] Revoke photo consent — verify the photo is gone from kiosk and exports
- [ ] `6.11` Multi-model review (GLM 5.2 / Kimi K3 / GPT Sol) with identical structured scope `SEC §7.3`
- [ ] `6.12` ⚠ Every finding has a reproduction against a live instance before it enters the fix list
- [ ] `6.13` Findings deduplicated across models, triaged, remediated
- [ ] `6.14` Retest of all remediated findings
- [ ] `6.15` ✅ **Gate: launch**

---

## Open Decisions

Mirrors `§10`. Tick when answered, and record the answer inline.

- [ ] `D1` Attendance capture — roster only, or kiosk too? *(assumed: both, roster first)* — affects `1.7`
- [ ] `D2` Booking — drop-in or reserved with capacity? — blocks `4.3.12`
- [ ] `D3` Payroll — calculate amounts or report hours only? — blocks `3.7.2`
- [x] `D4` Recurring billing — **decided: invoice + reminder + parent pays**
- [ ] `D5` Cash significance — if majority, cash receipting needs equal polish — affects `2.3.2`
- [x] `D6` Chinese variant — **decided: `zh-Hans` (Simplified)**
- [x] `D7` Licence — **decided: AGPL-3.0-or-later + commercial exception.** `LICENSE` is the verbatim FSF text; the exception offer and reasoning are in README §Licence. ⚠ Consequence: contributions need a CLA or an equivalent relicensing grant, or the exception cannot be sold over them — recorded in CONTRIBUTING.md.
- [x] `D8` Multi-style — **assumed: configurable ladders from day one, seeded Shotokan**
- [ ] `D9` Federation affiliation reporting needed? — affects `4.3.7` priority
- [ ] `D10` Pilot dojo identified and committed? — **blocks the Phase 1 exit gate**
- [x] `D11` Governance model — **decided: both, toggleable**
- [x] `D12` Hosting — **decided: managed per-client VPS; self-host published best-effort**
- [x] `D13` Mobile — **decided: PWA now; Capacitor wrap later against the triggers in §13.9**
- [x] `D14` WhatsApp — **decided: deferred. Email + Telegram at launch.**
