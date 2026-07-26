# DojoMaster — Build TODO

Working checklist for the whole project. Designed so **any agent can pick up mid-stream** without the original conversation.

**Read first:** [project_plan.md](project_plan.md) · [security_and_compliance.md](security_and_compliance.md) · [competitive_analysis.md](competitive_analysis.md)

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

- **Current phase:** Phase 0 — in progress
- **Last completed task:** `0.3.5` (audit helper + middleware) and `0.3.2` (soft delete), both now genuinely tested
- **Next task:** `0.3.7` — `Setting` model + hierarchy resolver
- **Test suite:** 160 passing, 7 skipped. `make test` / `.venv\Scripts\python -m pytest`
- **Remaining in Phase 0:** `0.1.6`, `0.3.7`, `0.3.8` ⚠, `0.3.9` ⚠, `0.4.4`, `0.6.2` ⚠ – `0.6.7`, `0.7.1` – `0.7.4`
- **Open questions blocking work:** `D7` (licence) blocks the LICENSE file only. Nothing else in Phase 0 is blocked.
- **Deviations from the plan so far:**
  - `Person` was made a `SoftDeleteModel` rather than a plain `TenantScopedModel`. Rationale: student records are attached to attendance, rank awards and invoices, all of which are evidence; erasure requests go through redaction, not DELETE. Consistent with plan §2 ("never hard-delete user data"). Migration `identity/0002`.
  - `0.3.6` (Money) and `0.3.1` (BaseModel) were marked `[DS]` but built in-house — everything else depends on them, so the parallel track would have blocked.

### ⚠ Concurrency protocol — two agents are working this repo

Both a primary agent and a delegated agent (OpenCode/MiniMax) have written to this
working tree simultaneously. That has already caused one duplicate implementation
(`0.3.4` was written twice, as `test_scoping_guard.py` and `test_unscoped_guard.py`;
the duplicate was removed) and two tasks ticked before they were finished.

**Rules from here:**

1. **Claim before you build.** Add your task id to the Claimed list below *before*
   writing code. If it is already claimed, pick another.
2. **Tick only what is tested.** A model class existing is not the task done. If
   the task says "model + write helper + middleware", all three plus tests.
3. **Never rewrite TODO.md wholesale** — surgical edits only. The other agent is
   editing it too.
4. **Prefer separate files over shared ones.** If you must edit a shared file
   (`config/settings/base.py`, `pyproject.toml`), make the smallest possible edit.
5. Better still: give each agent its own **git worktree or branch** and merge.
   Shared-tree concurrency works, but it is luck, not design.

**Claimed right now:** _(none — both agents idle)_

---

## Non-Negotiable Conventions

Apply to every task, including delegated ones. Violating these is the most likely way a multi-agent handoff produces a broken codebase.

- [ ] **Tenancy** — every model carries `organization` (directly or via an unambiguous FK chain). No exceptions. `§7.2`
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
- [ ] `0.1.6` `[DS]` Pre-commit hooks (format, lint, secret scan)
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
- [ ] `0.3.7` `Setting` model + resolver for the hierarchy `org → dojo → class → session → student` `§13.2`
- [ ] `0.3.8` ⚠ Field-level encryption helper (envelope, per-org data key, keys outside DB) `SEC §2.3`
- [ ] `0.3.9` ⚠ `Document` model + validated upload (magic bytes, size cap, generated names, outside web root), permission-checked serving view, EXIF stripping, SVG rejected `SEC §2.3`

### 0.4 i18n scaffolding
- [x] `0.4.1` `[DS]` `LocaleMiddleware`, locale paths, `USE_I18N`
- [x] `0.4.2` `[DS]` Locale stubs: `en`, `km`, **`zh-Hans`** `§13.4`
- [x] `0.4.3` `[DS]` `Person.locale` field + per-request locale resolution from the logged-in person
- [ ] `0.4.4` `[DS]` Khmer font bundled + `lang` attributes + line-break CSS; verify wrapping in tables and buttons `§13.4`
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
- [ ] `0.6.2` ⚠ TOTP 2FA, mandatory for org/dojo admin and any financial or PII-export role `SEC §2.1`
- [ ] `0.6.3` Recovery codes (generate once, show once, hashed at rest)
- [ ] `0.6.4` Session config: HttpOnly, Secure, SameSite=Lax, idle timeout, absolute cap, rotate on privilege change
- [ ] `0.6.5` Rate limiting + progressive lockout: login, reset, PIN, API
- [ ] `0.6.6` ⚠ Password reset: single-use, short-lived, no user enumeration (response *and* timing)
- [ ] `0.6.7` Security headers + strict CSP with nonces, no `unsafe-inline` `SEC §2.4`

### 0.7 Developer experience
- [ ] `0.7.1` `[DS]` Seed command: 2 orgs (one of each governance model), 3 dojos, 200 students, 2 years of attendance, ranks, invoices
- [ ] `0.7.2` `[DS]` Demo reset command (idempotent, safe to cron)
- [ ] `0.7.3` `backup` / `restore` management commands (pg_dump + media tarball) `§7.3`
- [ ] `0.7.4` First-run wizard: create org, first dojo, admin user, choose governance model

---

## Phase 1 — MVP: Run One Real Dojo

**Goal:** the pilot dojo abandons their spreadsheet. `§8 Phase 1`
**Do not start Phase 2 until a real dojo is using this.**

### 1.1 Students & families
- [ ] `1.1.1` `[DS]` `StudentProfile` — status, home dojo, sizes, licence `§4.2`
- [ ] `1.1.2` ⚠ Medical fields (allergies, conditions, medications, doctor, `do_not_spar`) with field-level encryption `SEC §2.3`
- [ ] `1.1.3` `[DS]` `GuardianLink` — relationship, contact / emergency / financial / custody flags, independent of each other `§4.2`
- [ ] `1.1.4` Multiple guardians per student, each independently contactable `§2 item 26`
- [ ] `1.1.5` `[DS]` `EmergencyContact` (Person link or plain name/phone)
- [ ] `1.1.6` ⚠ `ConsentRecord` — versioned, granular, revocable, timestamped `§4.2`
- [ ] `1.1.7` ⚠ Medical consent collected as its own deliberate act, not bundled into terms `SEC §6.5`
- [ ] `1.1.8` Waiver flow: present versioned document, capture signature + IP + timestamp
- [ ] `1.1.9` `[DS]` Student list: filter by dojo, rank, status, age, attendance gap, unsigned waiver, expired licence `§2 item 25`
- [ ] `1.1.10` `[DS]` Saved filter segments, reusable
- [ ] `1.1.11` `[DS]` Student detail hub — header, pinned alerts, tabs (attendance / rank / notes / billing / documents / family)
- [ ] `1.1.12` Student lifecycle status transitions: prospect → trial → active → on_hold → lapsed → alumni `§2 item 7`
- [ ] `1.1.13` Bulk hold / resume (seasonal mass pauses) `§12.5`
- [ ] `1.1.14` Student photo upload + re-encode + consent gate

### 1.2 Ranks
- [ ] `1.2.1` `[DS]` `Style` model `§4.4`
- [ ] `1.2.2` `[DS]` `RankLadder` (adult / junior variants) `§4.4`
- [ ] `1.2.3` `[DS]` `Rank` — order, name, colour, stripes, min months, min classes, min age
- [ ] `1.2.4` ⚠ `StudentStyleTrack` — rank is **per style**, not per student `§4.2`
- [ ] `1.2.5` `[DS]` `RankAward` + derived `current_rank` (denormalised, recomputed on write)
- [ ] `1.2.6` `[DS]` Manual promotion flow with audit
- [ ] `1.2.7` `[DS]` **Bulk promotion** — 30 students in one action after a grading `§2 item 24`
- [ ] `1.2.8` ⚠ `InstructorProfile.max_grading_rank_id` — grading ceiling enforced `§4.2`
- [ ] `1.2.9` `[DS]` External / transfer-in rank recognition (`awarded_by_external_org`, recognised / provisional / not recognised) `§12.6`
- [ ] `1.2.10` Negating award record for rank stripping (never delete) `§12.6`
- [ ] `1.2.11` `[DS]` Seed a Shotokan adult ladder + a junior mon ladder as fixtures

### 1.3 Enrolment & transfers
- [ ] `1.3.1` `Enrollment` — student ↔ dojo, primary flag, status, dates `§4.3`
- [ ] `1.3.2` Multi-dojo enrolment (several active at once)
- [ ] `1.3.3` ⚠ Transfer flow: end old enrolment, create new, write `TransferRecord`. Never mutate history. `§4.3`
- [ ] `1.3.4` ⚠ Test: attendance/invoice history survives transfer intact
- [ ] `1.3.5` `[DS]` `InstructorAssignment` — person ↔ dojo

### 1.4 Scheduling
- [ ] `1.4.1` `[DS]` `ClassTemplate` — rrule, time, duration, room, capacity, rank/age bounds `§4.5`
- [ ] `1.4.2` ⚠ `ClassSession` materialisation job, rolling 90-day horizon (mind DST)
- [ ] `1.4.3` ⚠ `ClosurePeriod` (org/dojo, date range, reason, `suppress_billing`) consulted by the generator `§12.2`
- [ ] `1.4.4` `[DS]` Seed public holidays for Cambodia; make the set per-country data
- [ ] `1.4.5` ⚠ Template edit semantics: "this occurrence" vs "this and future" `§4.5`
- [ ] `1.4.6` `[DS]` Ad-hoc one-off sessions (no template)
- [ ] `1.4.7` `[DS]` Cancel session + reason + notification hook
- [ ] `1.4.8` `[DS]` Substitute instructor assignment
- [ ] `1.4.9` `[DS]` Calendar views: week/month, per dojo, filtered by instructor
- [ ] `1.4.10` `ClassTemplate.counts_toward[]` tags — which class types count for which eligibility rules `§2 item 23`

### 1.5 Attendance — core
- [ ] `1.5.1` ⚠ `AttendanceRecord` with `client_generated_id`, idempotent upsert `§4.5`
- [ ] `1.5.2` `[DS]` Instructor roster UI — mobile, large targets, mark-all-present then deselect *(critical UX path: review against the 30-second target in `1.6.6` before ticking)*
- [ ] `1.5.3` `[DS]` Status set: present / late / absent / excused / visiting
- [ ] `1.5.4` `[DS]` Visiting-student attendance at a non-enrolled dojo
- [ ] `1.5.5` Retroactive edit with audit trail
- [ ] `1.5.6` ⚠ **Catch-up flow** — sessions in the last 14 days with no records, fast bulk entry, nag the instructor `§12.7`

### 1.6 Attendance — offline PWA
*Hardest correctness area in the project. None of this is delegable.*
- [ ] `1.6.1` PWA shell: manifest, service worker, installable
- [ ] `1.6.2` IndexedDB queue for pending attendance writes
- [ ] `1.6.3` ⚠ Sync endpoint, idempotent on `client_generated_id`
- [ ] `1.6.4` Conflict handling + visible sync state ("3 pending")
- [ ] `1.6.5` ⚠ Test: full class marked with network disabled, syncs correctly on reconnect
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
- [ ] `1.8.1` `[DS]` `Note` — polymorphic subject, visibility levels, pinned `§4.7`
- [ ] `1.8.2` ⚠ Visibility enforcement: `private` / `instructors` / `admins` / `parent_visible`
- [ ] `1.8.3` `[DS]` Pinned notes surface on the student header
- [ ] `1.8.4` ⚠ Safeguarding notes restricted to a named role, encrypted, access-logged `SEC §4`

### 1.9 Instructor time (basic)
- [ ] `1.9.1` `[DS]` `InstructorProfile` — pay type, rate, currency `§4.2`
- [ ] `1.9.2` `[DS]` `TimeEntry` model + `pay_rate_snapshot` `§4.8`
- [ ] `1.9.3` `[DS]` Auto-draft entry when a session's attendance is completed
- [ ] `1.9.4` `[DS]` Weekly timesheet view for the instructor

### 1.10 Import (acquisition tooling)
- [ ] `1.10.1` `[DS]` Generic CSV importer: upload → column mapping → preview → dry run `§12.10`
- [ ] `1.10.2` ⚠ Idempotent re-import (fix and re-run, don't duplicate)
- [ ] `1.10.3` `[DS]` Import students + guardians
- [ ] `1.10.4` `[DS]` Import historical attendance
- [ ] `1.10.5` `[DS]` Import rank history
- [ ] `1.10.6` `[DS]` Named presets for common competitor export formats
- [ ] `1.10.7` `[DS]` Import report: created / updated / skipped / errored, downloadable

### 1.11 Core reports
- [ ] `1.11.1` `[DS]` Attendance summary by dojo / class / period
- [ ] `1.11.2` `[DS]` Active students by rank
- [ ] `1.11.3` `[DS]` Attendance drop-off alert list (no attendance in N days)
- [ ] `1.11.4` `[DS]` CSV export on every report

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
- [ ] `D7` Licence — AGPL + commercial exception, open-core, or proprietary? — **blocks `0.1.1`**
- [x] `D8` Multi-style — **assumed: configurable ladders from day one, seeded Shotokan**
- [ ] `D9` Federation affiliation reporting needed? — affects `4.3.7` priority
- [ ] `D10` Pilot dojo identified and committed? — **blocks the Phase 1 exit gate**
- [x] `D11` Governance model — **decided: both, toggleable**
- [x] `D12` Hosting — **decided: managed per-client VPS; self-host published best-effort**
- [x] `D13` Mobile — **decided: PWA now; Capacitor wrap later against the triggers in §13.9**
- [x] `D14` WhatsApp — **decided: deferred. Email + Telegram at launch.**
