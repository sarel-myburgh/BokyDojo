# DojoMaster — Project Plan

A Student Information System (SIS) for martial arts organisations: multi-dojo attendance, ranking, scheduling, billing, and a parent portal. Self-hostable via Docker short term; managed single-tenant SaaS long term.

> Companion documents:
> - **[competitive_analysis.md](competitive_analysis.md)** — what the ~20 existing products do well and badly, and where DojoMaster fits.
> - **[security_and_compliance.md](security_and_compliance.md)** — threat model, security controls, AI prompt-injection defence, GDPR/hosting posture, pentest scope.

---

## 1. Goals & Non-Goals

### Goals
- Be the single source of truth for **who trains where, how often, at what rank, and whether they've paid**.
- Work for a **single independent dojo** (one tenant, one dojo) *and* a **federation/association** with many sub-dojos under one org.
- Deployable by a moderately technical dojo owner: `docker compose up`, one config file.
- Usable on a phone, in a training hall, by an instructor with chalky hands and bad wifi.

### Non-Goals (explicitly out of scope)
- Full accounting/GL (export to Xero/QuickBooks/CSV instead).
- Full-featured e-commerce (Shopify-grade). The shop is an order form tied to student accounts.
- Video/technique library or online course delivery (v3 at earliest).
- Tournament bracket/scoring software (integrate/export, don't rebuild).

---

## 2. What You Missed

You covered the spine well. These are the gaps that will bite, roughly in order of how badly.

### Critical (must be in MVP or you can't run a real dojo)

| # | Gap | Why it matters |
|---|---|---|
| 1 | **Grading/examination workflow** | You have "belt level" but not how it changes. Need: eligibility rules (min months at rank, min classes attended since last grading, min age), grading events, registration + fee, examiner panel, pass/fail/partial, certificate number, rank history. This is the *most karate-specific* thing in the product and the biggest reason a dojo picks you over generic gym software. |
| 2 | **Medical info, emergency contacts, waivers** | Legally required in practice. Allergies, conditions, medications, injury notes, "do not spar" flags, signed liability waiver on file with date + version. Distinct from "contact details of parents". |
| 3 | **Consent records** | Photo/video consent, marketing consent, data-processing consent — especially for minors. Needs to be per-person, versioned, timestamped, revocable. |
| 4 | **Person model that allows multiple roles** | An instructor is almost always also a student (training under the head sensei). A parent is often also a student. Two siblings share one payer. Model **one `Person` with role attachments**, not separate Student/Instructor/Parent tables — this is very painful to retrofit. |
| 5 | **Billing account / payer separate from student** | One invoice covering three siblings, sent to one parent, paid once. Bills attach to a *billing account*, not a student. Also unlocks sibling/family discounts. |
| 6 | **Attendance capture method** | You said "track attendance" but not *how*. Roster mark-off by instructor? Kiosk with QR/PIN self-check-in? Both? This drives the whole mobile UX. Must work **offline** and sync (dojo wifi is unreliable). |
| 7 | **Student lifecycle status** | Prospect → trial → active → on hold (injury/travel/exams) → lapsed → alumni. "On hold" in particular must pause billing without deleting history. Without this, your retention reporting and your billing are both wrong. |
| 8 | **RBAC scoped to org and dojo** | Org admin, dojo admin/head instructor, instructor, assistant instructor, front desk, parent, adult student. An instructor at Dojo B must not see Dojo A's students or anyone's payment data. Non-trivial with multi-dojo enrolment — design it up front. |
| 9 | **Configurable rank ladders per style** | Karate ≠ BJJ ≠ Judo ≠ Taekwondo. Even within karate: Shotokan vs Goju kyu counts differ. Also **kids' junior grades / mon grades / stripes** — a 7-year-old has "yellow belt + 2 stripes". Hard-coding a belt enum will block your second customer. |
| 10 | **Audit log** | Who changed a rank, deleted an attendance record, wrote off an invoice. Needed for trust, for disputes, and for your own support burden. |

### Important (v1.x, will be asked for within months)

| # | Gap | Notes |
|---|---|---|
| 11 | **Instructor credentials & expiry tracking** | Coaching certification, first-aid/CPR, child-protection/background check, federation instructor licence — each with expiry dates and reminders. Safeguarding requirement in most jurisdictions and a genuine selling point. |
| 12 | **What time tracking *feeds*** | You track instructor hours — then what? Pay rates (hourly vs per-class vs salary vs revenue share), approval workflow, payroll period export. Decide now whether you're doing payroll *calculation* or just *reporting*. Recommend: calculate, export CSV, never actually pay anyone. |
| 13 | **Federation/association membership** | Annual licence numbers, affiliation body, renewal dates, per-member annual fees passed up to the national body. Very common in karate; often the org's main admin headache. |
| 14 | **Communications** | Class cancellation ("no class Monday, flooding"), grading announcements, payment reminders, birthday messages. Channels: email + **Telegram** (dominant in Cambodia) + optionally SMS. Log every send. |
| 15 | **Class capacity, booking & waitlists** | Some dojos cap kids' classes. Decide if classes are drop-in (attendance only) or bookable (reservation + attendance). Recommend: attendance-only in MVP, booking behind a per-dojo flag later. |
| 16 | **Trial / prospect pipeline** | Free trial class → follow-up → conversion. Lightweight CRM. This is where dojo revenue actually comes from, and no competitor does it well. |
| 17 | **Curriculum/syllabus data** | Kata, kihon, kumite, terminology requirements per rank. Makes lesson plans and grading eligibility meaningful instead of free text, and enables "what does this student still need for green belt". |
| 18 | **Multi-currency & tax/receipts** | Cambodia runs dual USD/KHR. Need currency per dojo, exchange-rate handling on invoices, sequential receipt numbering, and VAT/tax fields for compliant invoices. |
| 19 | **Reporting** | Attendance trends, retention/churn by dojo and cohort, revenue per dojo, outstanding AR ageing, instructor hours, grading pass rates. Ship a handful of fixed reports; don't build a BI tool. |
| 20 | **Data export & backup** | Self-hosters need a one-command backup/restore. Everyone needs full CSV/JSON export (both for portability trust and for GDPR-style requests). |
| 21 | **Localisation** | Khmer language + Khmer script fonts, date formats, name ordering. At minimum: build i18n-ready from day one, ship English first. |
| 22 | **iCal feed / calendar subscription** | Cheap to build, parents love it. Read-only `.ics` per dojo and per student. |

### Added from competitive research (see [competitive_analysis.md](competitive_analysis.md))

These are gaps in the *existing market*, not just in your brief — each maps to a repeated complaint about a shipping competitor.

| # | Requirement | Driven by |
|---|---|---|
| 23 | **Per-class-type weighting in grading eligibility** — "40 classes since last grading, of which ≥10 must be kata classes" | Sharpest specific complaint found; no product models it |
| 24 | **Bulk operations everywhere** — promote 30 students after a grading in one action, bulk message, bulk invoice, bulk waiver send | Repeated con across Gymdesk and others |
| 25 | **Rich filtering/segmentation** — by age, rank, dojo, attendance gap, unsigned waiver, expired licence — and save filters as reusable segments | Named con for multiple products; blocks everyday work |
| 26 | **Multiple guardian contacts with independent delivery** — both parents get the email, separately | Competitors store one parent email; generates real parent complaints |
| 27 | **Stackable discounts** — sibling + annual prepay + hardship simultaneously, with a family cap | Competitors allow only one discount type at a time |
| 28 | **Customisable promotion criteria per rank**, not a fixed global rule | Directly requested in reviews |
| 29 | **One-command, complete, documented data export** — treat it as a headline feature | Lock-in and export difficulty are top-3 category complaints |
| 30 | **Expense tracking (light) + accounting export** that isn't manual re-entry | Requested; QuickBooks integrations widely criticised as manual |
| 31 | **Event pages** for gradings/seminars/camps — register, pay, roster | Requested; no good option exists |

### Nice-to-have / later

- Tournament & event registration (divisions by age/weight/rank, results feeding back into student profile).
- Belt/certificate PDF generation with org branding.
- Student self-service progress view ("techniques remaining to next grade").
- Attendance streaks / gamification for kids.
- Equipment/uniform size history on the student record (drives shop suggestions).
- Multi-org "federation view" — reporting across independently-run member dojos.
- Document vault (ID/passport/birth certificate scans for tournament age verification).

---

## 3. Personas & Roles

| Role | Scope | Can do |
|---|---|---|
| **Org Admin** | Whole organisation | Everything: create dojos, manage billing config, see all data, manage users, org-wide reports |
| **Dojo Admin / Head Instructor** | One or more dojos | Manage students, classes, schedules, gradings, instructors, invoices for their dojo(s) |
| **Instructor** | Assigned classes/dojos | Take attendance, write lesson plans, add student notes, log own time, view roster (no financial data) |
| **Assistant Instructor** | Assigned classes | Take attendance, log own time, read-only roster |
| **Front Desk / Admin Staff** | One or more dojos | Enrolments, contact details, payments, shop orders (no rank changes) |
| **Parent / Guardian** | Own children | View attendance, rank, schedule, invoices; pay; update contact details; order from shop |
| **Adult Student** | Self | Same as parent, for themselves |

Notes:
- Permissions are `(role, scope)` pairs — a Person can hold several. "Instructor at Dojo A + Dojo Admin at Dojo B + Student at Dojo A" must be expressible.
- Financial data is a **separate permission bit**, not implied by seniority.
- Student notes have visibility levels: `private-to-author`, `instructors`, `admins`, `visible-to-parent`. Default to `instructors`.

---

## 4. Domain Model

### 4.1 Core hierarchy

```
Organization (tenant root)
 └── Dojo (many)
      ├── Location / address, timezone, currency, contact
      ├── ClassTemplate (recurring class definition)
      │    └── ClassSession (materialised occurrence)
      │         ├── AttendanceRecord
      │         ├── LessonPlan (attached)
      │         └── TimeEntry (instructor hours)
      ├── Enrollment  (Student ↔ Dojo)
      └── InstructorAssignment (Person ↔ Dojo)
```

### 4.2 People

**`Person`** — one row per human. Never duplicated across roles.
`id, org_id, given_name, family_name, preferred_name, dob, gender, email, phone, address, photo, locale, created_at, ...`

Role profiles hang off it:

- **`StudentProfile`** — `person_id, status, home_dojo_id, joined_at, medical_notes, allergies, conditions, doctor_contact, do_not_spar, shirt_size, gi_size, federation_licence_no, licence_expiry`
- **`StudentStyleTrack`** — `student_person_id, style_id, ladder_id, current_rank_id, started_on, status`
  - **Rank is per style, not per student.** A student can be 3rd kyu in karate and a blue belt in BJJ at the same organisation, simultaneously, progressing independently. Putting `style_id`/`current_rank_id` on the student record breaks the moment your org teaches two arts — which is most orgs. Also handles a junior crossing from the kids' ladder to the adult ladder at 16: close one track, open another, both retained.
- **`InstructorProfile`** — `person_id, bio, pay_type (hourly|per_class|salary|volunteer), pay_rate, currency, employment_start, max_grading_rank_id`
  - Instructors have rank via `StudentStyleTrack` like everyone else — they're students too. `max_grading_rank_id` is the **grading ceiling**: most organisations only let an instructor examine candidates some fixed distance below their own rank, with senior grades reserved for the chief instructor or a panel. Model it or your grading module will be politically unusable.
- **`GuardianLink`** — `guardian_person_id, student_person_id, relationship, is_primary_contact, is_emergency_contact, is_financially_responsible, has_custody, notes`
  - A student may have 0..n guardians (0 for adults). Financial responsibility is separate from contact and from custody — divorced-parent cases are common and messy.
- **`EmergencyContact`** — may be a Person link or a plain name/phone for non-users.
- **`Credential`** — `person_id, type (first_aid|coaching|background_check|instructor_licence), issuer, reference, issued_on, expires_on, document_id` → drives expiry reminders.
- **`ConsentRecord`** — `person_id, type (photo|marketing|data_processing|waiver), version, granted, granted_at, granted_by_person_id, ip, document_id`

### 4.3 Multi-dojo membership (the part to get right)

**`Enrollment`** — `id, student_person_id, dojo_id, is_primary, status (active|on_hold|ended), started_on, ended_on, hold_reason, notes`

- A student has **one primary/home dojo** and **zero or more additional active enrollments**. Primary drives default billing, reporting attribution, and "which dojo owns this student".
- **Transfer** = end the old enrollment (with `ended_on` + reason) and create a new one, recorded in **`TransferRecord`** (`student, from_dojo, to_dojo, effective_on, reason, approved_by`). Never mutate the existing row — attendance history stays attached to the dojo where it happened.
- Attendance, invoices, and time entries all carry `dojo_id` so history survives transfers intact.
- Cross-dojo attendance ("visiting another dojo for a seminar") is just an attendance record at a dojo the student isn't enrolled in — allowed, flagged as `visiting`.

### 4.4 Ranking

- **`Style`** — `org_id, name` (Shotokan Karate, BJJ, Judo…)
- **`RankLadder`** — `style_id, name, applies_to (adult|junior)` — juniors get their own ladder with mon grades/stripes.
- **`Rank`** — `ladder_id, order, name, belt_colour, stripe_count, min_months_at_previous, min_classes_since_previous, min_age`
- **`RankAward`** — `student_person_id, rank_id, awarded_on, awarded_by_person_id, grading_event_id?, certificate_no, notes` — **rank is derived from the latest award**, not stored as a mutable field (keep a denormalised `current_rank_id` for query speed, recomputed on write).
- **`GradingEvent`** — `dojo_id (or org-wide), date, venue, examiners[], fee, status`
- **`GradingRegistration`** — `event_id, student_id, target_rank_id, eligible (computed), eligibility_overridden_by, fee_invoice_id, result (pass|fail|partial|pending), score_sheet (jsonb), examiner_notes`
- **`CurriculumItem`** — `style_id, category (kata|kihon|kumite|terminology|theory), name, description, media_url` with **`RankRequirement`** joining item ↔ rank. Feeds lesson plans and grading sheets.

### 4.5 Scheduling & attendance

- **`ClassTemplate`** — `dojo_id, name, style_id, rrule (RFC 5545 recurrence), start_time, duration, room, capacity, default_instructor_ids[], rank_min, rank_max, age_min, age_max, active_from, active_to`
- **`ClassSession`** — materialised occurrence: `template_id?, dojo_id, starts_at, ends_at, instructor_ids[], status (scheduled|cancelled|completed), cancellation_reason, lesson_plan_id`
  - Generate sessions on a rolling horizon (e.g. 90 days ahead) via a background job. Ad-hoc one-off sessions have no template.
  - Editing a template offers "this occurrence / this and future" semantics — decide this early, it's the classic calendar-app trap.
- **`AttendanceRecord`** — `session_id, student_person_id, status (present|late|absent|excused|visiting), marked_by, marked_at, method (roster|kiosk_qr|kiosk_pin|self|import), client_generated_id (for offline dedupe), note`
  - `client_generated_id` + idempotent upsert is what makes offline sync work. Add it from day one.

### 4.6 Lesson plans

- **`LessonPlan`** — `org_id, dojo_id?, author_id, title, target_ranks[], duration_minutes, objectives, is_template, tags[]`
- **`LessonPlanBlock`** — `plan_id, order, minutes, title, description, curriculum_item_ids[]` (warm-up → kihon → kata → kumite → cool-down)
- Plans live in a shared library; attaching one to a `ClassSession` copies a snapshot so later edits don't rewrite history.

### 4.7 Notes

**`Note`** — `subject_type (student|session|enrollment|invoice), subject_id, author_id, body, visibility, pinned, created_at`
Polymorphic, append-only-ish (edits keep a revision trail), with visibility as above. Pinned notes surface on the student header — "severe nut allergy", "father not authorised for pickup".

### 4.8 Instructor time

**`TimeEntry`** — `instructor_person_id, dojo_id, session_id?, category (class|admin|private_lesson|event|travel), started_at, ended_at, minutes, source (clock|manual|auto_from_session), status (draft|submitted|approved|rejected), approved_by, pay_rate_snapshot, notes`

- Auto-create a draft entry when an instructor completes a session's attendance; they can adjust before submitting.
- `pay_rate_snapshot` freezes the rate at approval time so rate changes don't rewrite history.
- Payroll period report → CSV. Do **not** build actual payment disbursement.

### 4.9 Billing

- **`BillingAccount`** — `org_id, payer_person_id, currency, balance, tax_id, billing_address, dunning_state`
- **`BillingAccountMember`** — `account_id, student_person_id` (siblings on one account)
- **`Plan`** — `org_id, dojo_id?, name, type (recurring|class_pack|drop_in|one_off), amount, currency, interval (monthly|term|annual), classes_included, dojo_scope (single|all_dojos)`
- **`Subscription`** — `billing_account_id, student_person_id, plan_id, status (active|paused|cancelled), started_on, paused_from/to, next_bill_on, discount_id`
- **`Discount`** — percentage/fixed, reasons: sibling, multi-dojo, hardship/scholarship, annual prepay, staff/family
- **`Invoice`** — `number (sequential per org), billing_account_id, dojo_id, issued_on, due_on, currency, subtotal, tax, total, status (draft|open|paid|part_paid|overdue|void|written_off)`
- **`InvoiceLine`** — `invoice_id, description, source_type (subscription|grading|shop_order|drop_in|late_fee|manual), source_id, qty, unit_amount, tax_rate`
- **`Payment`** — `invoice_id?, billing_account_id, amount, currency, method (payway|cash|bank_transfer|card_manual), received_on, reference, recorded_by`
- **`PaymentAttempt`** — gateway-facing: `provider, provider_txn_id, status, raw_request, raw_response, callback_payload, hash_verified` — keep the full audit trail, you *will* need it for reconciliation disputes.
- **`CreditNote`** / **`Refund`** — never delete an invoice; void or credit it.

**Billing behaviours needed:** proration on mid-period join, pause/hold (injury, travel, school exams — extremely common, and the #1 reason dojos abandon rigid software), family caps, drop-in billing generated from attendance, overdue reminders (dunning ladder), write-offs with reason codes, and **cash payments** — most dojos in the region are still substantially cash, so manual payment recording with a receipt must be first-class, not an afterthought.

### 4.10 Shop

- **`Product`** (gi, belt, mitts, patch), **`ProductVariant`** (size/colour, SKU, price), **`StockLevel`** (per dojo — inventory lives at a dojo, not centrally)
- **`Order`** — `billing_account_id, dojo_id, status (pending|paid|ready_for_pickup|collected|cancelled), fulfilment (pickup|delivery), notes`
- **`OrderItem`** — variant, qty, unit price snapshot
- Orders generate invoice lines on the student's existing billing account — one payment flow, not two.
- Keep it deliberately simple: **pickup at dojo is the default**, no shipping engine, no payment split. Allow backorder/pre-order since gi sizes are always out of stock.

### 4.11 Cross-cutting

- **`AuditLog`** — `org_id, actor_person_id, action, subject_type, subject_id, before (jsonb), after (jsonb), ip, at`
- **`Document`** — waivers, certificates, ID scans, medical letters; `subject_type/subject_id, filename, mime, size, storage_key, uploaded_by, retention_until`
- **`Announcement` / `MessageLog`** — audience selector (org / dojo / class / rank / individual), channel, body, sent_at, delivery status per recipient
- **`Setting`** — per-org and per-dojo key/value overrides

---

## 5. Functional Modules

### 5.1 Students
Search/filter by dojo, rank, status, age, attendance drop-off. Student profile page as the hub: header (photo, rank, home dojo, pinned alerts), tabs for Attendance, Rank history, Notes, Billing, Documents, Family. Bulk actions: mark on hold, message, add to grading.

### 5.2 Attendance
- **Instructor roster view** — big touch targets, whole class on one screen, offline-first, one tap per student, "mark all present" then deselect absentees.
- **Kiosk mode** — tablet at the door, student enters PIN or scans a QR/card. Locked-down browser mode.
- Late/excused states, retroactive editing (with audit), bulk import from CSV for migration.
- Attendance drives: grading eligibility, drop-in billing, retention alerts ("hasn't attended in 21 days").

### 5.3 Scheduling
Week/month calendar per dojo, instructor-filtered view, cancel-with-notification, substitute instructor assignment, public read-only timetable page per dojo (also useful as marketing), iCal feed.

### 5.4 Gradings
Eligibility computed from rules + manual override with reason. Registration list → fee invoicing → score sheets on the day (offline-capable) → results → automatic rank award + certificate number → notification to parents.

### 5.5 Lesson plans
Library with tags and rank targeting, duplicate-and-edit, attach to session, printable/phone view. Keep the editor dumb — structured blocks + rich text. Instructors will not use anything more complicated than this.

### 5.6 Instructor time & payroll
Clock in/out or auto-from-session, weekly timesheet, submit → dojo admin approves → payroll period CSV.

### 5.7 Billing
Invoice list with AR ageing, subscription runs (scheduled job), payment recording (cash/transfer/gateway), reminders, statements per billing account.

### 5.8 Parent portal
Deliberately narrow: my children's attendance, current rank + progress to next, upcoming schedule, invoices + pay button, shop, update contact/medical details, consent toggles, announcements. Mobile-first PWA. No app store presence in v1.

### 5.9 Shop
Catalogue per dojo, cart, order → invoice → pay or pay-at-dojo, pickup tracking, low-stock alert for admins.

### 5.10 Reports
Fixed set: attendance summary, retention/churn, revenue by dojo, AR ageing, instructor hours, grading results, active students by rank. All exportable to CSV.

---

## 6. Payments — ABA PayWay

**Verify all of this against ABA's current merchant documentation before building.** From the integration docs available publicly, PayWay generally offers:

- Hosted checkout / purchase API with request signing (HMAC hash over concatenated fields using a merchant secret)
- `merchant_id`, unique `tran_id` per transaction, amount, currency (**USD and KHR**)
- Payment options: ABA PAY (app deeplink), KHQR, and cards (Visa/Mastercard/UnionPay)
- `continue_success_url` (browser redirect) plus a server-to-server **pushback/callback URL**
- A transaction status/check API for reconciliation

### Design decisions this forces

1. **No card-on-file auto-charge — confirmed as the model.** Subscriptions are **auto-generated invoice + reminder + parent taps Pay**. `Subscription` is therefore an *invoice-generation schedule*, not a payment mandate. This makes the dunning/reminder ladder load-bearing rather than an edge case: reminders at issue, due−3 days, due date, +7, +14, then escalation to the dojo admin. Multi-channel (email + Telegram) matters more here than in an auto-debit world, because collection depends entirely on getting a human's attention. True auto-debit stays possible as a later provider capability without reshaping the domain.
2. **Abstract behind a `PaymentProvider` interface** from day one (`createCheckout`, `handleCallback`, `verifyTransaction`, `refund`). Implement `AbaPayway`, `Manual` (cash/bank transfer), and `Stripe` (for the eventual non-Cambodia SaaS customer). The e-shop and invoices both go through it.
3. **Never trust the browser redirect.** Only the verified server-side callback (hash-checked) marks an invoice paid. Store the raw callback payload.
4. **Idempotency.** Callbacks retry. Key on `tran_id`, make handling idempotent, log every attempt.
5. **Reconciliation report** — daily list of PayWay transactions vs recorded payments, with mismatches highlighted. You will need this in week one of real usage.
6. **Currency**: store amounts in minor units as integers with an explicit currency code. Never floats. Dual USD/KHR means an exchange rate snapshot on every KHR-settled USD invoice.

---

## 7. Architecture

### 7.1 Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **Django 5 + Django REST Framework** | CRUD-heavy, permission-heavy, admin-heavy — Django's admin, auth, migrations and ORM cut months off this. Batteries you'd otherwise hand-roll. |
| DB | **PostgreSQL 16** | jsonb for score sheets/settings, real constraints, easy backup |
| Frontend (admin/instructor) | **HTMX + Alpine.js + Tailwind**, server-rendered | Small team, mostly forms and tables. Avoids maintaining a separate SPA. |
| Offline attendance | Small **PWA** with IndexedDB queue + idempotent sync endpoint | The one place that genuinely needs client-side state |
| Parent portal | Same Django app, separate URL namespace + theme, PWA-installable | One codebase, one auth system |
| Background jobs | **Celery + Redis** (or `django-q2` to avoid Redis for small self-hosters) | Session generation, invoicing runs, reminders, callbacks retry |
| Files | Local volume by default, S3-compatible optional | Self-hosters shouldn't need object storage |
| Email | SMTP config, provider-agnostic | Self-hosters bring their own |
| Deploy | Docker Compose: `web`, `worker`, `db`, `redis`, `caddy` | Caddy gives automatic HTTPS with one line |

**Alternative if you'd rather stay in TypeScript:** Next.js + Prisma + Postgres. Faster for the parent portal UI, materially slower for the admin CRUD surface — you'd rebuild a lot of what Django gives free. Pick based on which language you'll actually maintain for five years, not on benchmarks.

### 7.2 Tenancy

- Even with container-per-client, **keep `organization_id` on every table** and enforce scoping in a base queryset/manager. Costs almost nothing now; without it, the first customer who wants two orgs, or any future shared-instance deployment, is a rewrite.
- Sub-dojos are rows, not tenants. Scoping is `org → dojo → resource`.
- Self-host = one container set, one org, no cross-tenant routing.
- SaaS = same image, per-client compose stack, per-client Postgres, subdomain routing at a shared reverse proxy. Same code path, different orchestration.

### 7.3 Self-host requirements
- `docker compose up` with a `.env`; first-run wizard creates the org, first dojo, and admin user.
- `manage.py backup` / `restore` wrapping `pg_dump` + media tarball; documented restore drill.
- Migrations run automatically on container start, with a documented rollback.
- Health endpoint, structured logs to stdout, sane resource footprint (target: runs on a 2 GB VPS).
- Demo/seed data command for evaluation.
- **Pick a licence deliberately now** — AGPL with a commercial exception, or open-core (core self-hostable, SaaS-only features like multi-org reporting reserved). This decision shapes what you can charge for later and is annoying to change after publishing.

### 7.4 Security & privacy
This system holds medical data on children. Treat that as the headline constraint.

- Argon2 password hashing, TOTP 2FA for admin roles, session timeout.
- Field-level encryption for medical notes and any ID documents.
- Every access to a minor's record audit-logged.
- Data retention policy per record type; automated purge of ex-students after a configurable window.
- Full per-person export and deletion (GDPR-style), even if not legally required in your launch market — SaaS customers in other jurisdictions will demand it.
- Rate limiting on login and parent-portal endpoints.
- No PII in URLs, logs, or analytics.
- Documents served through permission-checked views, never direct static URLs.

---

## 8. Roadmap

### Phase 0 — Foundations (3–4 weeks)
Repo, Docker Compose, CI, Postgres, auth + 2FA, Person + Organization + Dojo models, **governance-model toggle**, RBAC scaffolding + **permission matrix test fixture**, audit log, i18n scaffolding (en/km/zh catalogues wired, strings extracted from day one), opaque IDs, seed data.
**Exit:** deployable empty app with login and an org/dojo/person admin, and a passing permission-matrix suite.

### Phase 1 — MVP: run one real dojo (7–11 weeks)
Students + guardians + medical/emergency + waivers/consent · `StudentStyleTrack` + configurable rank ladders + rank history + manual promotion · class templates + session generation + closure calendar · **offline attendance roster** · **kiosk (photo grid / name list, optional PIN)** · notes · basic instructor time tracking · enrolments, multi-dojo, transfers · **CSV importer with competitor presets** · core reports.
**Exit:** your pilot dojo stops using their spreadsheet. **Get this into a real dojo's hands before building anything in Phase 2.**

### Phase 2 — Money (4–6 weeks)
Billing accounts, plans, subscriptions, invoices, discounts, holds · cash/manual payments + receipts · **ABA PayWay integration** + callbacks + reconciliation · dunning reminders · AR reporting.
**Exit:** the pilot dojo collects fees through the system.

### Phase 3 — Parents & instructors (5–7 weeks)
Parent portal (attendance, rank, schedule, invoices, pay, details, consents) · **full en/km/zh localisation of both portals** · announcements + **notification providers (email + Telegram)** with per-recipient, per-category preferences · instructor→parent comments · lesson plan library · grading workflow end-to-end (eligibility rules, per-class-type weighting, grading ceiling) · instructor timesheet approval + payroll export · iCal feeds · public timetable pages.
**Exit:** parents self-serve; you stop answering "when is grading" by phone.

### Phase 4 — Shop, API & polish (5–7 weeks)
**Public REST API + OpenAPI + scoped tokens + webhooks** · product catalogue + per-dojo stock + orders + pickup · credentials/expiry tracking · curriculum items + rank requirements · makeup credits · private lessons · prospect/trial pipeline · certificate PDF generation · reporting expansion (class fill rates).

### Phase 4.5 — Optional AI (2–3 weeks, only after §5 of the security doc is implemented)
BYOK provider config · translation assist · scoped read-only analytics Q&A · adversarial injection corpus in CI · spend caps and kill switch.
**Gate:** does not ship until the injection corpus passes and the tool catalogue is proven scope-safe.

### Phase 5 — Managed hosting readiness (6–8 weeks)
Provisioning automation (client stack, DNS, TLS, backups) · **fleet security: per-client credentials, no shared keys, bastion/VPN** · centralised monitoring, alerting, log shipping · **automated backup restore verification** · staged upgrade pipeline (canary → cohort → fleet) · billing *for* your hosting service · DPA/sub-processor/TOMs paperwork · public demo instance · docs site.
**This phase is the product, not polish** (§13.10).

### Phase 6 — Security gate
Full pentest per [security_and_compliance.md](security_and_compliance.md) §7, including multi-model review. Remediate, retest, then launch.

**Rough total to managed-hosting-ready: 8–12 months of focused part-time work.** Phases 1–2 alone (~4 months) give a genuinely useful product.

---

## 9. Positioning

From the competitive research, three structural advantages fall out of decisions you'd already made — worth naming explicitly so they drive the build rather than being discovered later.

1. **Self-hosting answers the category's top complaints by construction.** Per-student price escalation, auto-renewing contracts, cancellation friction, data-export difficulty, and (in Mindbody's case) ads served to your own customers — all moot when the dojo runs the container. Lead with this.
2. **Multi-dojo as a default, not an enterprise tier.** Every competitor either ignores multi-location or gates it behind enterprise pricing and a sales call. A four-dojo association with 150 students total currently has no good option.
3. **Regional fit is an uncontested wedge.** Every serious competitor assumes USD, Stripe, English and card-on-file. KHR/USD dual currency, ABA PayWay, cash-first receipting, Telegram and Khmer are unserved. Small market, but yours to lose — and a far better beachhead than fighting Gymdesk head-on.

**Deliberately not building:** marketing automation / lead-gen CRM (Spark and ATLAS own it, needs scale), a website builder (ship a good public timetable page and embeddable signup form instead), app-store mobile apps (PWA suffices), consumer marketplace.

**Pricing, if/when SaaS:** competitors sit at $49–$250/mo scaling with student count. Go **flat per dojo, unlimited students** — roughly $25–50/dojo/month with association volume breaks. It matches your actual cost structure (per container, not per student) and the headline writes itself against the #1 complaint in the category.

**The binding constraint is support, not code.** Responsive human support is the most-praised attribute across every competitor, and support decay after growth is the most damaging failure mode. For a one-person operation this caps how many SaaS clients you can carry, probably well before the tech does. Price and pace for that.

---

## 10. Key Decisions Needed From You

1. **Attendance capture** — instructor roster only, or kiosk self-check-in too? (Research says kiosk is a top-praised feature; recommend both, roster first.)
2. **Booking** — are classes drop-in, or do students reserve spots with capacity limits?
3. **Payroll** — calculate pay amounts, or just report hours?
4. ~~Recurring billing model~~ — **decided: invoice + reminder + parent pays.**
5. **Cash handling** — how significant? If it's the majority, cash receipting needs to be as polished as the online flow, not a fallback.
6. **Languages at launch** — English only, or English + Khmer from day one?
7. **Licence** — AGPL + commercial exception, open-core, or fully proprietary with a self-host tier?
8. **Multi-style** — karate only at launch, or configurable rank ladders from day one? (Recommend: configurable from day one, seed with a Shotokan ladder.)
9. **Federation affiliation** — do your target orgs report membership up to a national body? If yes, that's a Phase 3 feature, not a nice-to-have.
10. **Pilot dojo** — who is it, and can you commit to their real data by end of Phase 1?

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Scope creep — every module here could be its own product | Hard phase gates. Nothing from Phase 3+ starts until a real dojo runs on Phase 1–2. |
| Crowded field — 20+ vendors, several well-funded | Compete only on the uncontested intersection (self-host + multi-dojo-native + regional), never on feature count. |
| Support load caps growth before technology does | Ruthless docs and self-service; price SaaS to reflect a hard client ceiling; automate diagnostics. |
| Instructors won't adopt anything slower than a paper roster | Attendance must be under 30 seconds for a 20-student class, offline. Test with real instructors, not yourself. |
| ABA PayWay integration surprises (sandbox access, recurring support, settlement timing) | Get merchant/sandbox access and build a throwaway spike **before** Phase 2 planning is finalised. |
| Small market → low revenue per customer | Keep per-client ops cost near zero: identical images, automated provisioning, no bespoke per-client code. Ever. |
| Minors' medical/PII data breach | Encryption, audit logging, minimal retention, 2FA for admins — from Phase 0, not retrofitted. |
| Self-host support burden eats the business | Ruthless docs, one supported deployment path, a diagnostic command that dumps versions/config/health for support tickets. |
| Container-per-client upgrades drift out of sync | Version pinning, automated migration testing, a staged rollout script. Solve before customer #3. |

---

## 12. Second-Pass Gaps

Found on a second review of the domain. The first is a blocking question — it changes the data model, not just the feature list.

### 12.1 ⚠ Branch model or federation model? (needs an answer before Phase 0 ends)

"Main organisation has multiple dojos underneath" is ambiguous between two structures that need different software:

**(a) Branch model** — one business, several locations. Central ownership, central billing, org admin sees all revenue. Staff are employees. This is what §4 currently assumes.

**(b) Federation model** — an association of **independently owned** dojos sharing a syllabus, a grading authority, and a brand. The org sets curriculum, ratifies ranks, holds the instructor register, and collects annual affiliation fees — but each dojo's fees, revenue, and student contact details are its own private business, and the head of the association explicitly must *not* see them.

The differences are structural, not cosmetic:

| | Branch | Federation |
|---|---|---|
| Who sees revenue | Org admin sees all | Each dojo only; org sees affiliation fees only |
| Who owns student PII | Org | Dojo (org gets rank data only) |
| Grading authority | Convenience | The *whole point* — org ratifies, dojo cannot self-award |
| Billing | One merchant account | One merchant account **per dojo** |
| Instructor employment | Employees, org payroll | Independent, org tracks credentials only |
| A dojo leaving | Doesn't happen | Must be supported: export their data, revoke access, keep rank records |

Karate associations in particular skew heavily to (b). If you need both — likely — then `Organization` needs a `governance_model` flag, financial visibility must be a permission that federation orgs simply don't grant upward, and dojos need their own payment-provider credentials rather than inheriting the org's. **Cheap now, structural surgery later.**

### 12.2 Holiday calendars and closures
Session generation will happily create classes on New Year, Khmer New Year, Pchum Ben, and the week the dojo is shut for the owner's holiday. Need a **`ClosurePeriod`** (org- or dojo-scoped, date range, reason, `suppress_billing` flag) consulted by the session generator, plus a seeded public-holiday set per country. Missing this produces phantom classes, phantom absences, and corrupted grading eligibility counts.

### 12.3 Makeup classes and credits
When a class is cancelled, students expect a makeup or a credit — near-universal dojo policy, absent from the plan. Needs: cancellation reason driving a policy (`makeup_token` | `account_credit` | `nothing`), a token with an expiry, and redemption tracked on attendance.

### 12.4 Private lessons
A significant revenue line at most dojos and completely unmodelled. Needs a session type booked 1:1 against an instructor, its own rate, and often an instructor commission split. Slots into `ClassSession` with `type=private` plus a `PrivateLessonBooking`, and connects to the `TimeEntry` category already defined.

### 12.5 Seasonality
Dojos are seasonal businesses: September enrolment surge, summer collapse, term-based programmes, mass simultaneous holds in the holidays. Consequences: bulk hold/resume operations, term-based plan types alongside monthly, and retention reporting that compares year-over-year rather than month-over-month (otherwise every August looks like a catastrophe).

### 12.6 Rank recognition on transfer-in
A student arriving from another school or association with an existing rank. Needs `RankAward` to support `awarded_by_external_org` with no internal grading event, a "recognised / provisional / not recognised" status, and a note field. Also: honorary and posthumous grades, and (rarely, but it happens) rank stripping. Make `RankAward` support a negating record rather than a delete.

### 12.7 Retroactive attendance, because instructors forget
Assume the roster does *not* get taken during class perhaps 20% of the time. Needs: a "catch up attendance" flow listing sessions in the last 14 days with no records, fast bulk entry from memory, and a nag to the instructor rather than to the admin. Without this, attendance data quality collapses and every downstream feature — eligibility, drop-in billing, retention alerts — becomes untrustworthy.

### 12.8 Kiosk UX for children who can't read or type
A meaningful share of students are 5–8 years old. PIN entry doesn't work for them. The kiosk needs a **photo grid** — tap your own face — scoped to the class starting now, with search only as a fallback for adults. This makes student photos operationally required, not decorative, which in turn makes photo consent (§2) load-bearing rather than paperwork. Printable QR student cards are a good secondary path.

### 12.9 Email deliverability is a trap for self-hosters
A self-hosted box sending payment reminders from a residential or cheap-VPS IP lands in spam or is blocked outright. Since your entire billing model is "invoice + remind", **deliverability is a revenue feature**. Mitigations: require an external SMTP provider during setup rather than offering local sending, validate SPF/DKIM at first-run and refuse to proceed silently, and treat **Telegram as the primary channel** where it's culturally dominant — it's free, instant, has read receipts, and bypasses the problem entirely.

### 12.10 Import tooling is your customer acquisition mechanism
Every prospective customer arrives carrying a mess: a Gymdesk or Zen Planner export, three spreadsheets, or a paper folder. Given that difficult data export is a top-three complaint in this category, a **good importer is a sales weapon, not a chore**. Build: generic CSV mapper with preview and dry-run, named presets for the main competitors' export formats, and idempotent re-import so a botched attempt can be corrected rather than restarted. Put this in Phase 1, not Phase 5.

### 12.11 QA, because bugs are what killed the incumbents
Instability is complaint #4 in the category and it's what turned Zen Planner's reputation. This software fails in front of 30 waiting children at 5pm. Minimum: end-to-end tests over the attendance and check-in paths, a realistic seed dataset (200 students, 3 dojos, 2 years of history), migration tests against a restored production-shaped dump, and a staging environment that mirrors a real client. Cheaper than the reputation.

### 12.12 Backups that are verified, not just documented
Self-hosters have no backups until the day they discover they have no backups. Ship: a backup command enabled by default on a schedule, a `restore --verify` that restores into a throwaway container and asserts row counts, an offsite target (S3/rclone/Backblaze) configured at first run, and a nag banner in the admin UI when the last successful verified backup is older than N days.

### 12.13 A public demo instance
Seeded, reset nightly, no signup. Cheap to run off the same image, and it's the single highest-leverage sales asset for a product nobody has heard of. Also doubles as your staging environment.

### 12.14 Class fill-rate and instructor reporting
For a multi-dojo owner the operationally valuable question isn't "how many students" but "which class slots are dying". Attendance as a percentage of capacity by class, timeslot, and instructor, trended. Cheap to build on data you already hold, and it's what justifies the subscription to whoever signs the cheque.

### 12.15 The owner *is* the front desk
Most dojos have no admin staff. The owner teaches five nights a week and does administration at 10pm on a phone. This means **the admin interface must be as mobile-competent as the parent portal** — not a desktop-first back office with a responsive afterthought. Design invoicing, enrolment, and messaging for a thumb.

### 12.16 Cambodian invoice and tax compliance
Verify before Phase 2: GDT requirements for invoice content and sequential numbering, whether Khmer-language invoices are mandatory, VAT registration thresholds, and what constitutes a valid receipt for a customer who needs one. Get this wrong and your invoices are decorative. Worth an hour with a local accountant rather than an afternoon of my guessing.

### 12.17 The name
`DojoMaster` sits in a crowded namespace — Dojo Manager, DojoExpert, DojoTrack, Dojo Champ, dojomanagementsoftware.com all exist. Worth a trademark search before you print anything. Separately: *dojo* is a Japanese term. BJJ academies, taekwondo *dojangs*, and kung fu *kwoons* may read the name as not-for-them, which narrows your market perception at zero benefit. Something style-neutral costs nothing now and something later.

### 12.18 Be honest about who runs their own box
A dojo owner who can run Docker Compose is rare. Realistically the self-host tier serves *you*, a handful of technical owners, and your credibility — it is not a customer acquisition channel. That's fine and worth building anyway (it's your architecture, your dogfood, your anti-lock-in story), but don't let it distort the roadmap: the **managed-hosting offering is where actual users will be** (see §13.9), so provisioning and operations tooling matters more than a polished self-host installer.

---

## 13. Confirmed Decisions — Round 2

### 13.1 Governance model toggle `DECIDED`

`Organization.governance_model` ∈ `{central, federated}`, set at creation, changeable only by a migration-style admin action (it moves data ownership).

| Behaviour | `central` | `federated` |
|---|---|---|
| Org admin sees dojo revenue | Yes | **No** — affiliation fees only |
| Org admin sees full student PII | Yes | Rank + attendance summary only; contact details stay at dojo |
| Payment provider credentials | Org-level, inherited | **Per dojo**, independently configured |
| Grading authority | Dojo may self-award within instructor ceiling | Org ratifies; dojo submits results for approval |
| Instructor records | Employment + payroll | Credential register only |
| Dojo departure | N/A | Supported: export dojo's data, revoke org access, retain ratified rank records |

Implementation: a single `visibility_policy` resolver consulted by every queryset, not scattered `if` statements. Both models share one schema — `federated` withholds fields rather than storing them elsewhere. Write the permission matrix as a test fixture early; it's the thing most likely to leak.

### 13.2 Attendance & kiosk configuration `DECIDED`

Settings resolve through a hierarchy, each level overriding the last where permitted:

```
Org default → Dojo → ClassTemplate → ClassSession (instructor, live) → Student (individual override)
```

**`kiosk_display_mode`** ∈ `{photo_grid, name_list, both}`
- `photo_grid` — tap your own face. Required for young children; also the fastest for everyone.
- `name_list` — searchable names, better for large adult classes and students without a photo on file.
- Instructor can flip this **live at the kiosk** without an admin, because reality varies by class.

**`pin_policy`** ∈ `{off, optional, required}`
- Set per class *and* overridable per student (a specific student may be required to PIN even when the class isn't — useful where a parent disputes attendance, or for an adult who wants their check-ins non-spoofable).
- Resolution rule: **the stricter of class policy and student override wins.** A class set to `off` cannot downgrade a student marked `required`.
- PINs are 4–6 digits, hashed, rate-limited, lockout after N failures, and resettable by a dojo admin. They are a convenience control, not a security boundary — never let a PIN alone unlock anything beyond marking oneself present.

**Kiosk hardening:** device-bound token rather than a user session, scoped to one dojo, no access to any record beyond the roster of sessions starting within ±X minutes, no PII beyond first name and photo on screen, auto-return to the grid after each check-in, and a physical-presence assumption (never expose kiosk endpoints to the open internet without the device token).

Photo grid makes student photos operationally required — so photo consent (§2.3) gates it, and a student without consent falls back to name entry automatically.

### 13.3 Notifications: email, Telegram, WhatsApp `DECIDED — with a cost caveat`

Per-recipient channel preferences (not per student — a student may have two guardians who each want a different channel), with per-category granularity: `billing`, `attendance`, `grading`, `schedule_change`, `announcement`, `instructor_comment`.

| Channel | Reality |
|---|---|
| **Email** | Always available, always the fallback. Deliverability is the risk (§12.9) — external SMTP mandatory. |
| **Telegram** | Free, instant, no approval process, read receipts, dominant in Cambodia. Requires the user to `/start` the bot once — handle that onboarding link in the portal. **Make this the default channel.** |
| **WhatsApp** | ⏸ **Deferred.** Not free and not simple: the Business Platform requires a Meta Business account, business verification, a dedicated phone number, and **pre-approved message templates** for anything business-initiated. Outside a 24-hour user-initiated window you can only send approved templates, and you pay **per conversation**, priced by country and category. Revisit when a client will fund it. |

Design consequence: build a `NotificationChannel` provider interface exactly like `PaymentProvider` — `send(recipient, template, context)`, capability flags (`supports_rich_text`, `requires_template_approval`, `cost_per_message`), per-org credentials. **Ship Email + Telegram.** The interface means adding WhatsApp later is a plugin, not a refactor — so deferring it costs nothing.

Every send is logged with channel, template, status, and cost. Failed delivery on the preferred channel falls back to email automatically.

### 13.4 Internationalisation `DECIDED`

Launch: **English, Khmer, Chinese**. Architecture must make adding a language a translation-file drop, never a code change.

- Standard gettext-style catalogues (`django.po`), no hardcoded strings anywhere, `lazy` translation in models.
- **Chinese variant: `zh-Hans` (Simplified)** `DECIDED`. `zh-Hant` remains a catalogue drop away if a cohort needs it.
- **Khmer needs specific care**: no inter-word spaces, so line-breaking and truncation need `lang="km"` plus a font with proper shaping (Noto Sans Khmer / Battambang). Test wrapping in tables and buttons early — it breaks naive CSS.
- Language is a **per-person** setting (`Person.locale`), not per org. A Khmer-speaking parent and an English-speaking instructor in the same dojo each get their own. Applies to both portals and to every notification template.
- Also localise: date formats, currency display, name ordering (family-name-first for Chinese and Khmer), and number formatting.
- Translator workflow: keep catalogues in the repo, accept community PRs, and consider Weblate later if volunteers appear.

### 13.5 Instructor comments to parents `DECIDED`

Extends the existing `Note` model with `visibility = parent_visible`. Behaviour:
- Writing one optionally triggers a notification on the guardian's preferred channel.
- Parent-visible notes are **immutable after 24 hours** and edits leave a visible trail — this protects both sides in a dispute.
- Parents can reply; replies land in the instructor's inbox, are logged, and are visible to dojo admins. This is a safeguarding requirement, not a feature: **no private unlogged channel between an instructor and a child's family.**
- Template snippets for common comments ("great focus today", "needs to practise kata at home") so instructors actually use it.

### 13.6 Optional BYOK AI integration `DECIDED — design in security_and_compliance.md §5`

Org owner supplies their own API key (OpenRouter, OpenCode Zen, or any OpenAI-compatible endpoint — configurable base URL + model name, so new providers need no code change). **Off by default.** Two features:

1. **Translation assist** — machine-translate instructor comments and announcements into the recipient's language. Always shown labelled and alongside the original; never silently replaces it.
2. **Analytics Q&A for the org owner** — "how many students are ready for grading", "which dojo is performing best".

**Prompt injection is the central risk and it is treated as a fully solved architectural problem, not a prompt-wording problem.** Student names, notes, and contact fields are attacker-controlled text: a parent can name their child `Ignore previous instructions and…`. The defence is that **the model has no authority to lose**:

- No text-to-SQL. The model selects from a **fixed catalogue of parameterised, read-only query tools** with validated enum/range arguments.
- Tenant and dojo scoping is injected **server-side from the session** and is not a parameter the model can influence. It is structurally incapable of requesting another org's data.
- No tool mutates state. No sends, no writes, no payments. Output is advisory text only.
- Aggregates by default; row-level PII requires a separate permission and is redacted otherwise.
- Output rendered as sanitised plain text/markdown — no HTML, no auto-linking, no image loading.
- Per-org spend caps, rate limits, a kill switch, and a full audit log of question → tools → arguments → answer.
- Admin/owner roles only. **Never exposed in the parent portal.**

Full threat model and control list in [security_and_compliance.md](security_and_compliance.md).

> ⚠ **GDPR interaction:** enabling this sends data to a third-party LLM provider, making them a sub-processor. Default to sending aggregates only; require an explicit, separately-recorded opt-in before any student PII leaves the server; and surface the provider in the client's sub-processor list. See §6 of the security doc.

### 13.7 Tax, VAT and payment gateways — configurable, expandable `DECIDED`

**Tax** is a per-org (overridable per dojo) **`TaxProfile`**: country, tax name and registration number, rate table by product/service category, inclusive-vs-exclusive pricing, rounding rule, invoice numbering scheme (prefix, sequence, per-dojo or per-org, reset period), and required invoice fields. Cambodia ships as a seeded profile; adding a country is data, not code. Historic invoices snapshot the profile that applied at issue.

**Payment gateways** use the `PaymentProvider` registry from §6, extended with capability flags:
`supports_refund`, `supports_partial_refund`, `supports_recurring`, `supports_qr`, `supports_preauth`, `currencies[]`, `countries[]`.
Credentials are per-org or per-dojo (mandatory per-dojo in `federated` mode). Ship `Manual` (cash/transfer) + `AbaPayway`; add Stripe, Wing, ACLEDA, PayPal as demand appears. The UI must render whatever the active provider declares it can do rather than hardcoding ABA's flow.

### 13.8 Public API `DECIDED — and it's a competitive weapon`

Given that data lock-in is a top-three complaint in this category (see competitive analysis §3.3), a real API is marketing, not just plumbing.

- **REST + OpenAPI 3 spec**, auto-generated from serializers, with a browsable docs page. GraphQL is not worth the complexity here.
- **Scoped API tokens** — per integration, per org/dojo, read/write per resource, expiring, revocable, with last-used tracking. Never a "god key".
- **Webhooks** — `student.enrolled`, `attendance.recorded`, `invoice.paid`, `rank.awarded`, `grading.completed`, etc. HMAC-signed payloads, retry with exponential backoff, delivery log the client can inspect.
- **Versioned** (`/api/v1/`), with a written deprecation policy. Self-hosters skip versions; don't strand them.
- Rate limited per token; every call in the audit log.
- The **bulk export endpoint is part of the public API**, not a hidden admin function. Advertise it.
- Same API powers your own frontends — dogfooding keeps it honest.

### 13.9 Mobile: PWA now, native later — with a defined trigger `RECOMMENDATION`

Build **mobile-first, desktop-friendly** across both portals and the admin (§12.15 — the owner does admin at 10pm on a phone). Ship a PWA. Do **not** start a native app now: it doubles surface area, adds two release pipelines and app-store review latency, and buys little at your stage.

Revisit when any of these actually bites:
1. **iOS push proves unreliable** — web push on iOS requires the user to add the site to their home screen first, and adoption of that is poor. If payment reminders depend on push, this becomes the blocker.
2. **Offline attendance outgrows IndexedDB** — unlikely, but if sync reliability in the hall is still a problem after tuning, native storage is more predictable.
3. **App-store presence becomes a sales objection** — some clients equate "real product" with "in the App Store".

When you do: **wrap the existing PWA with Capacitor** rather than rewriting. One codebase, native push and storage, store presence, weeks not months. Design the PWA now so that path stays open — no server-side-rendering dependencies in the portal shell.

### 13.10 Managed hosting model `DECIDED — see security doc §6 for the compliance analysis`

Clients don't self-host; they buy hosting from you. Per-client VPS, provisioned and operated by you, backed up and updated by you. This is how most SIS vendors in this space actually operate, and it neatly resolves §12.18.

Operationally this means Phase 5 (provisioning, monitoring, backup verification, staged upgrades) is not optional polish — it *is* the product. Budget for it accordingly. The open self-host path remains published and supported-on-a-best-effort basis: it's your anti-lock-in credibility and costs little once the image exists.
