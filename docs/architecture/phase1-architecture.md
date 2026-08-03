# Nick Cosmetics — Retail Management Platform
## Phase 1: System Architecture (Python-Only Stack)

> **Scope note:** No SQL schema file was attached to the brief, despite references to a "supplied schema." Section 4 designs the schema from the stated business requirements. If you have an existing schema, share it and I'll produce a diff/migration plan instead of a fresh design.

---

## 1. Architecture Overview

### 1.1 Guiding Pattern
A **modular monolith with a clean-architecture core**, not microservices. At 1 branch / 3–5 staff, microservices add operational overhead (multiple deployments, network latency, distributed transactions) with no real benefit. The clean-architecture core means the *business logic doesn't care* how many branches exist — so when you do expand, you scale the monolith horizontally or peel off services later without a rewrite.

### 1.2 Connectivity model (per client)
Not every client needs offline support, and forcing one connectivity model onto all of them adds complexity without matching benefit:

| Client | Connectivity | Why |
|---|---|---|
| **Desktop POS** | Offline-first (local SQLite, syncs to cloud when internet returns) | The till has to keep ringing up sales through a power/internet outage — this is the one place "no connection" is a real, frequent scenario worth engineering around. |
| **Mobile app (staff/manager)** | Cloud-only, over **any** internet connection (WiFi or mobile data) | Phones already have their own data plans; there's no shop-WiFi dependency, no LAN pairing/QR step, no local server to keep running. If the phone has signal, the app works. |
| **Web admin, e-commerce, customer portal** | Cloud-only | Browser-based, always assumes internet by nature. |

This deliberately drops a shop-WiFi-tethered "local server + QR pairing" pattern (seen in comparable inventory apps) in favor of keeping mobile simple and internet-agnostic, while concentrating true offline engineering effort where it's actually needed: the desktop till.

```
┌─────────────────────────────────────────────────────────────┐
│                      CLIENTS                                  │
│  Desktop POS (offline-first,  │  Web Portal  │  Mobile App    │
│  local SQLite)                │  E-commerce  │  (cloud-only,  │
│                                │  Customer Portal│ any internet)│
└───────────────┬─────────────────────────────┬─────────────────┘
                │ Local SQLite, syncs          │ REST/JSON over HTTPS
                │ to cloud when online         │ directly, always
                ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    API GATEWAY LAYER                          │
│         FastAPI application (auth, routing, rate limit)       │
├─────────────────────────────────────────────────────────────┤
│                    APPLICATION LAYER                          │
│   Services: Inventory, Sales, Customers, Suppliers, Sync,     │
│   Reporting, Users/Auth, Notifications                        │
├─────────────────────────────────────────────────────────────┤
│                      DOMAIN LAYER                              │
│   Entities, Value Objects, Domain Events, Business Rules       │
├─────────────────────────────────────────────────────────────┤
│                  INFRASTRUCTURE LAYER                          │
│   SQLAlchemy Repositories, Alembic Migrations, Celery Tasks,   │
│   Redis Cache/Queue, S3-compatible Object Storage, Payment     │
│   Gateway Adapters (Paystack/Flutterwave)                      │
└───────────────┬─────────────────────────────┬─────────────────┘
                ▼                              ▼
      PostgreSQL (cloud, source of truth)   Redis (cache/queue)
```

### 1.3 Why this shape
- **Single source of truth**: one PostgreSQL database in the cloud. Mobile, web, and e-commerce clients talk to it directly via the API; the desktop POS syncs to it from a local SQLite cache when internet is available.
- **Domain layer has zero framework dependencies** — no FastAPI, no SQLAlchemy imports inside `domain/`. This is what lets you swap the API framework or ORM later without touching business rules, and what makes the domain logic reusable inside the offline desktop app.
- **One shared domain package** (`nickcosmetics_core`) is used by both the cloud API and the offline desktop app, so "what counts as a valid sale" or "how stock deducts on checkout" is defined once, not twice.

---

## 2. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Backend API | **FastAPI** | Async, native Pydantic validation, auto OpenAPI docs, best-in-class Python API performance |
| ORM | **SQLAlchemy 2.0** (async) | Mature, supports both PostgreSQL and SQLite — critical since offline desktop uses SQLite with the same models |
| Migrations | **Alembic** | Standard companion to SQLAlchemy |
| Cloud DB | **PostgreSQL 16** | Relational integrity, JSONB for flexible fields, strong concurrency |
| Local DB (offline) | **SQLite** (via SQLAlchemy) | Zero-config, file-based, works fully offline |
| Caching / Queue broker | **Redis** | Session cache, rate limiting, Celery broker |
| Background jobs | **Celery** (or **Dramatiq** if you want fewer moving parts) | Sync reconciliation, email/SMS notifications, report generation |
| Auth | **JWT** via `python-jose`, **passlib[bcrypt]** | Stateless auth for multi-client system |
| Desktop app | **PySide6 (Qt for Python)** | Native performance, proper offline SQLite integration, good for POS-style UI with barcode scanner input; alternative: **Flet** if you want faster iteration with a more web-like dev experience |
| Mobile app | **Flet** or **BeeWare (Briefcase/Toga)** | Keeps mobile in Python instead of introducing React Native/Flutter — BeeWare compiles to native iOS/Android; Flet is faster to build but has a heavier runtime |
| E-commerce website | **Django** (separate app) or **FastAPI + Jinja2/HTMX** | Django is the pragmatic choice here: batteries-included admin, auth, ORM, and a mature payment/e-commerce ecosystem (django-oscar patterns) saves months vs. hand-rolling on FastAPI |
| Web Admin Portal | Server-rendered with **Django templates + HTMX + Alpine.js**, or a small **Flet** web app | HTMX keeps you Python-first on the frontend logic while still giving a modern, reactive feel without a JS framework/build step |
| Task scheduling | **Celery Beat** | Periodic sync jobs, expiry-alert checks, daily report generation |
| File/image storage | **S3-compatible** (AWS S3, or Backblaze B2 for lower cost in Nigeria) via `boto3` | Product images, invoices, receipts |
| Payments (Nigeria) | **Paystack** and/or **Flutterwave** Python SDKs | Local card/bank transfer support |
| Barcode | `python-barcode` (generation), `pyzbar` + camera or USB HID scanner (reading) | Both work fully offline on desktop |
| PDF generation | **WeasyPrint** or **ReportLab** | Receipts, invoices |
| Testing | **pytest**, **pytest-asyncio**, **factory_boy**, **httpx** (API testing) | |
| Containerization | **Docker** + **docker-compose** (dev), **Docker** images in production | |
| CI/CD | **GitHub Actions** | Lint (ruff), type-check (mypy), test, build, deploy |

**On the "no JS backend" constraint:** this is fully honored — FastAPI/Django handle all backend logic. HTMX/Alpine.js are optional *thin* client-side sprinkles for the admin portal (no build step, no Node toolchain, no backend logic in JS) — flag if you'd rather the admin portal also be a Flet desktop-style web app to stay 100% Python end-to-end; that's a valid and arguably cleaner choice for a small internal tool.

---

## 3. Repository / Folder Structure

Monorepo, since everything shares the domain core and the database.

```
nick-cosmetics-python-platform/
├── packages/
│   └── core/                        # Shared domain package (pip-installable)
│       ├── nickcosmetics_core/
│       │   ├── domain/               # Entities, value objects, domain events
│       │   │   ├── inventory/
│       │   │   ├── sales/
│       │   │   ├── customers/
│       │   │   └── users/
│       │   ├── application/          # Use cases / services (framework-agnostic)
│       │   │   ├── inventory_service.py
│       │   │   ├── sales_service.py
│       │   │   ├── sync_service.py
│       │   │   └── reporting_service.py
│       │   ├── interfaces/           # Repository/gateway abstract base classes
│       │   └── exceptions.py
│       └── pyproject.toml
│
├── apps/
│   ├── api/                          # Cloud FastAPI application
│   │   ├── main.py
│   │   ├── api/v1/
│   │   │   ├── routers/ (auth, products, inventory, sales, customers, sync, reports)
│   │   │   └── dependencies.py
│   │   ├── infrastructure/
│   │   │   ├── db/ (models, session, repositories — SQLAlchemy)
│   │   │   ├── cache/ (redis)
│   │   │   ├── storage/ (s3)
│   │   │   └── payments/ (paystack, flutterwave adapters)
│   │   ├── tasks/                    # Celery tasks
│   │   ├── alembic/
│   │   └── tests/
│   │
│   ├── desktop_pos/                  # PySide6 offline-first POS
│   │   ├── ui/ (screens, widgets)
│   │   ├── local_db/ (SQLite models mirroring core, sync client)
│   │   ├── sync/ (outbox pattern, conflict resolution)
│   │   └── tests/
│   │
│   ├── web_admin/                    # Django or FastAPI+HTMX admin portal
│   ├── ecommerce/                    # Django public storefront + customer portal
│   ├── mobile/                       # Flet/BeeWare staff+manager app
│   └── sync_service/                 # Standalone cloud-side sync reconciliation worker
│
├── infra/
│   ├── docker/ (Dockerfiles per app)
│   ├── docker-compose.dev.yml
│   └── k8s/ or railway/fly configs
│
├── docs/
│   ├── architecture/
│   ├── api/
│   └── adr/                          # Architecture Decision Records
│
├── .github/workflows/
└── README.md
```

---

## 4. Database Design

### 4.1 Design principles applied
- **Every table** gets `id (UUID)`, `created_at`, `updated_at`, `is_deleted` (soft delete) — required for reliable sync/conflict resolution later.
- **Branch-aware from day one**: even with 1 branch, every stock-bearing table carries a `branch_id` FK. Retrofitting multi-branch onto a single-branch schema is the #1 source of painful migrations — designing it in now costs almost nothing.
- **Money stored as integers (kobo)**, never floats — avoids rounding errors in financial reporting, standard practice for POS systems.
- **FIFO stock batches** (not a single quantity column) — cosmetics have expiry dates, so stock must be tracked by batch/lot, not just aggregate count.

### 4.2 Core schema (PostgreSQL, simplified DDL)

```sql
-- USERS & AUTH
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_id UUID REFERENCES branches(id),
    full_name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('super_admin','branch_manager','staff')),
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE branches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    is_warehouse BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- CATALOG
CREATE TABLE brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    parent_id UUID REFERENCES categories(id)   -- supports subcategories
);

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku TEXT UNIQUE NOT NULL,
    barcode TEXT UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    brand_id UUID REFERENCES brands(id),
    category_id UUID REFERENCES categories(id),
    status TEXT DEFAULT 'active' CHECK (status IN ('active','discontinued','draft')),
    unit_of_measure TEXT DEFAULT 'each',
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE product_variants (      -- shade/size variants
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id),
    variant_name TEXT NOT NULL,       -- e.g. "Shade 210", "50ml"
    sku TEXT UNIQUE NOT NULL,
    barcode TEXT UNIQUE,
    cost_price_kobo BIGINT NOT NULL,
    selling_price_kobo BIGINT NOT NULL,
    reorder_level INT DEFAULT 5,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE product_images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id),
    url TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    sort_order INT DEFAULT 0
);

-- SUPPLIERS & PURCHASING
CREATE TABLE suppliers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    contact_phone TEXT,
    contact_email TEXT,
    address TEXT,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id UUID REFERENCES suppliers(id),
    branch_id UUID REFERENCES branches(id),
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','ordered','partially_received','received','cancelled')),
    ordered_by UUID REFERENCES users(id),
    ordered_at TIMESTAMPTZ,
    expected_at DATE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE purchase_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_order_id UUID REFERENCES purchase_orders(id),
    variant_id UUID REFERENCES product_variants(id),
    quantity_ordered INT NOT NULL,
    quantity_received INT DEFAULT 0,
    unit_cost_kobo BIGINT NOT NULL
);

-- STOCK (FIFO BATCH TRACKING)
CREATE TABLE stock_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id UUID REFERENCES product_variants(id),
    branch_id UUID REFERENCES branches(id),
    purchase_order_item_id UUID REFERENCES purchase_order_items(id),
    batch_number TEXT,
    quantity_received INT NOT NULL,
    quantity_remaining INT NOT NULL,
    unit_cost_kobo BIGINT NOT NULL,
    expiry_date DATE,
    received_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE stock_adjustments (      -- damage, loss, manual correction
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_batch_id UUID REFERENCES stock_batches(id),
    adjusted_by UUID REFERENCES users(id),
    quantity_delta INT NOT NULL,        -- negative for loss/damage
    reason TEXT NOT NULL CHECK (reason IN ('damaged','expired','theft','recount','other')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- SALES
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name TEXT,
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    password_hash TEXT,                 -- null for walk-in/guest customers
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE customer_addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id),
    label TEXT,                          -- "Home", "Office"
    address_line TEXT NOT NULL,
    city TEXT, state TEXT, country TEXT DEFAULT 'Nigeria',
    is_default BOOLEAN DEFAULT FALSE
);

CREATE TABLE sales (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_id UUID REFERENCES branches(id),
    customer_id UUID REFERENCES customers(id),   -- null for walk-in
    served_by UUID REFERENCES users(id),
    channel TEXT NOT NULL CHECK (channel IN ('pos','website','mobile')),
    status TEXT DEFAULT 'completed' CHECK (status IN ('completed','refunded','partially_refunded','cancelled')),
    subtotal_kobo BIGINT NOT NULL,
    discount_kobo BIGINT DEFAULT 0,
    total_kobo BIGINT NOT NULL,
    payment_method TEXT CHECK (payment_method IN ('cash','card','transfer','online')),
    local_ref_id TEXT,                  -- offline-generated ID, for sync idempotency
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sale_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sale_id UUID REFERENCES sales(id),
    variant_id UUID REFERENCES product_variants(id),
    stock_batch_id UUID REFERENCES stock_batches(id),   -- which FIFO batch it was deducted from
    quantity INT NOT NULL,
    unit_price_kobo BIGINT NOT NULL,
    line_total_kobo BIGINT NOT NULL
);

CREATE TABLE refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sale_id UUID REFERENCES sales(id),
    processed_by UUID REFERENCES users(id),
    amount_kobo BIGINT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- AUDIT & SYNC
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    action TEXT NOT NULL,               -- 'product.update', 'sale.refund', etc.
    entity_type TEXT NOT NULL,
    entity_id UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,             -- identifies which desktop/branch instance
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    operation TEXT CHECK (operation IN ('insert','update','delete')),
    status TEXT CHECK (status IN ('pending','applied','conflict','failed')),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
```

### 4.3 Key improvements over a "typical" supplied schema
1. **FIFO batch tracking** instead of a flat `quantity` column on `products` — required for expiry tracking and accurate cost-of-goods-sold.
2. **Variants split from products** — a lipstick with 8 shades is one product, 8 variants; a flat schema usually forces duplicate "products" per shade, which breaks reporting.
3. **Money as `BIGINT` kobo**, not `DECIMAL`/`FLOAT` — eliminates float rounding bugs in financial totals.
4. **`local_ref_id` + `sync_log`** — without an idempotency key generated offline, a POS sale synced twice becomes a duplicate sale. This is the single most common bug in offline-first POS systems.
5. **Soft deletes everywhere relevant** — hard deletes break sync (a device offline for a week doesn't know a product was deleted, and re-creates it).
6. **`branch_id` on every operational table from day one** — see 4.1.
7. **Category self-reference for subcategories** (e.g. Skincare → Moisturizers) — cosmetics catalogs are rarely flat.

---

## 5. Module Breakdown

| Module | Responsibility |
|---|---|
| **Auth & Users** | JWT issuance/refresh, RBAC enforcement, password reset, session management |
| **Catalog** | Products, variants, brands, categories, images |
| **Inventory** | Stock batches, adjustments, transfers (future multi-branch), low-stock/expiry alerts |
| **Purchasing** | Suppliers, purchase orders, goods receipt |
| **Sales (POS)** | Cart, checkout, payment recording, receipts, returns/refunds |
| **Customers** | Profiles, addresses, order history, notifications |
| **E-commerce** | Storefront catalog view, cart, online checkout, order tracking |
| **Sync** | Outbox pattern on desktop, reconciliation service on cloud, conflict resolution |
| **Reporting** | Sales/inventory/customer reports, scheduled report generation |
| **Notifications** | Email/SMS for low stock, order status, password reset (Celery tasks) |
| **Audit** | Immutable action log across all modules |

---

## 6. API Architecture

- **REST + JSON**, versioned at `/api/v1/`, documented automatically via FastAPI's OpenAPI/Swagger.
- **Resource-oriented routes**: `/products`, `/products/{id}/variants`, `/sales`, `/sales/{id}/refund`, `/inventory/batches`, `/reports/sales?range=daily`.
- **Auth**: `Authorization: Bearer <JWT>`; short-lived access token (~15 min) + refresh token; role claims embedded in the JWT and re-validated server-side per request (never trust client-declared role).
- **Idempotency**: POST `/sales` requires a client-generated `local_ref_id` header; duplicate submissions with the same ref return the original result rather than creating a second sale — this is what makes offline sync safe.
- **Pagination**: cursor-based on list endpoints (`?cursor=...&limit=50`) rather than offset — offset pagination degrades on large sales history tables.
- **Rate limiting**: Redis-backed, per-user and per-IP, via `slowapi` (FastAPI's `flask-limiter` equivalent).
- **Webhooks**: inbound from Paystack/Flutterwave for payment confirmation, signature-verified.

---

## 7. Synchronization Architecture

This is the highest-risk part of the system, so it gets first-class design. This applies only to the **desktop POS**, per the connectivity model in Section 1.2 — the mobile app, being cloud-only, never needs offline sync; it reads/writes the cloud API directly.

**Pattern: Outbox + versioned reconciliation**

1. **On the desktop app**, every write (new sale, stock adjustment, etc.) is written to the local SQLite DB *and* appended to a local `outbox` table in the same transaction.
2. **A background sync worker** (runs on a timer, or on network-available events) reads unsynced outbox rows and POSTs them to `/api/v1/sync/push` in batches, tagged with `device_id` + `local_ref_id`.
3. **Server-side reconciliation**:
   - For **inserts** (e.g., a completed sale): applied idempotently via `local_ref_id` — if it already exists, it's a no-op, not a duplicate.
   - For **updates** (e.g., a product price change made both centrally and, in a rare case, cached locally): **last-write-wins by `updated_at`**, with a conflict record written to `sync_log` when the incoming version is older than the current server version, so nothing is silently dropped — a manager can review conflicts.
   - **Stock deduction is never resolved by last-write-wins.** Stock changes are treated as *deltas* (append-only ledger via `stock_adjustments` and batch deduction records), not overwrites — two branches independently selling the last unit while offline is resolved by replaying both deltas and flagging negative-stock results for manual reconciliation, rather than one sale silently overwriting the other.
4. **Pull**: after push, the device requests `/api/v1/sync/pull?since=<last_sync_timestamp>` to get server-side changes (new products, price updates from admin, etc.) and applies them locally.
5. **Conflict UI**: rare conflicts surface in the web admin portal for a manager to resolve, rather than being silently auto-resolved when money or stock counts are involved.

This is why the domain layer is shared (`nickcosmetics_core`) — the *rule* for "how a sale affects stock" is identical whether it runs against SQLite offline or PostgreSQL online, so there's no logic drift between the two.

---

## 8. Security Architecture

- **Authentication**: JWT (access + refresh), bcrypt password hashing (via `passlib`), account lockout after repeated failed logins.
- **Authorization**: RBAC enforced server-side per endpoint via FastAPI dependencies (`require_role("branch_manager", "super_admin")`), never trusted from the client. Staff-scoped queries (e.g. "my sales only") are filtered at the query level, not the UI level.
- **Transport**: HTTPS everywhere (Let's Encrypt / managed TLS termination at the load balancer).
- **Input validation**: Pydantic schemas on every endpoint; SQL injection is structurally prevented via SQLAlchemy's parameterized queries (no raw string SQL).
- **Rate limiting**: login endpoint and public e-commerce endpoints specifically, to blunt credential stuffing and scraping.
- **Audit logging**: every state-changing action writes to `audit_logs` with actor, action, entity, before/after where relevant.
- **Secrets management**: environment variables via `python-dotenv` locally, a real secrets manager (e.g., Railway/Fly secrets, or AWS Secrets Manager) in production — never committed.
- **Payment data**: never stored directly — Paystack/Flutterwave handle card data (PCI scope stays with them); only transaction references stored locally.
- **Backups**: automated daily PostgreSQL backups with point-in-time recovery; encrypted at rest.

---

## 9. Deployment Strategy

| Component | Recommendation |
|---|---|
| API + Web Admin + E-commerce (Django/FastAPI) | Containerized, deployed to **Railway** or **Fly.io** (both are low-friction for a small team, similar to what you're already using for `cosmetics-platform`) |
| PostgreSQL | Managed instance (Railway Postgres, Supabase, or Neon) with daily backups |
| Redis | Managed (Railway/Upstash) |
| Object storage | Backblaze B2 or AWS S3 (cheaper egress matters for Nigeria-based bandwidth costs) |
| Desktop POS | Packaged with **PyInstaller** into a Windows/macOS installer, auto-update via a lightweight version-check endpoint |
| Mobile app | Built via Flet/BeeWare, distributed through Play Store / TestFlight |
| CI/CD | GitHub Actions: lint → type-check → test → build Docker image → deploy on merge to `main` |
| Environments | `dev` → `staging` → `production`, with staging as a required gate before deploys touch the live POS/e-commerce |

---

## 10. Development Roadmap

**Phase 1 — Architecture (this document)** ✅

**Phase 2 — Foundations (2–3 weeks)**
- Core domain package (`nickcosmetics_core`)
- Database schema + Alembic migrations
- Auth (JWT, RBAC) end-to-end
- CI pipeline

**Phase 3 — Inventory & Catalog (2–3 weeks)**
- Products, variants, categories, brands, images
- Suppliers, purchase orders, stock batches, adjustments
- Low-stock/expiry alert jobs (Celery Beat)

**Phase 4 — POS + Offline Desktop (3–4 weeks)**
- PySide6 desktop shell, barcode scan flow, checkout
- Local SQLite mirror, outbox pattern
- Sync push/pull against the API

**Phase 5 — Web Admin Portal (2–3 weeks)**
- Dashboards, inventory management UI, reporting views, staff management

**Phase 6 — E-commerce + Customer Portal (3–4 weeks)**
- Storefront, cart, checkout, payment integration, order tracking, invoices

**Phase 7 — Mobile App (3–4 weeks)**
- Staff scan/search/sales-view, manager reports/approvals

**Phase 8 — Hardening & Launch (2 weeks)**
- Load testing, security review, staff training, pilot at the single branch, backup/DR drill

*(Total: roughly 4–5 months for one developer working steadily; compressible with more hands on Phases 4–7, which are largely parallelizable once Phase 2–3 land.)*

---

## 11. UI/UX Design Strategy

- **POS screen**: optimized for speed over aesthetics — large tap targets, barcode-scan-first workflow, keyboard-shortcut checkout for power users, receipt preview before print.
- **Admin portal**: dashboard-first (today's sales, low stock, pending purchase orders front and center), data-dense tables with filters rather than card layouts.
- **E-commerce site**: standard conversion-optimized storefront patterns — clear stock badges ("Only 3 left"), fast search, mobile-first given Nigerian mobile-dominant browsing.
- **Mobile staff app**: single-purpose screens (scan, search, my sales) — avoid trying to cram admin-portal density into a phone screen.
- **Consistent design tokens** (colors, spacing, type scale) shared as a small design-system doc so the desktop, web, and mobile surfaces feel like one product, not three.
- **Accessibility**: sufficient contrast, keyboard navigation on admin/e-commerce, since staff will use this daily for hours.

---

## 12. Critical Review — Gaps & Recommendations

Issues in the original brief worth flagging before implementation:

1. **No schema was actually supplied** — addressed above by designing one from requirements; confirm before Phase 2 migrations are written.
2. **"Branch Manager has almost identical permissions to Super Admin"** is a real security risk if branches expand — recommend a `permissions` join table (not just three hardcoded roles) so future roles (e.g., "Regional Manager") don't require a code change. Kept the three-role model for now per your spec, but the schema's `role` field should migrate to a proper RBAC permissions table before multi-branch rollout.
3. **Offline conflict resolution needs a UI**, not just backend logic — someone (a manager) needs a place to *see* and resolve sync conflicts, or they'll silently pile up. Included in Phase 5 scope.
4. **Returns/refunds interact with stock** — a refunded item should optionally return to a stock batch (if resellable) or go to `stock_adjustments` as damaged (if not) — this decision point wasn't in the original brief and needs a UI prompt at refund time.
5. **E-commerce and in-store price parity** — the brief doesn't say whether online prices can differ from in-store. Recommend explicit `channel_price` support per variant (defaulting to the same price) rather than assuming a single price forever — cosmetics businesses often run online-only promos.
6. **Tax/VAT handling** isn't mentioned — Nigerian VAT (7.5%) should be modeled explicitly on `sales` (a `tax_kobo` column) rather than folded into totals, for correct reporting and potential future FIRS e-invoicing compliance.
7. **Multi-currency** isn't mentioned but worth a note: if online sales ever target diaspora customers, storing all money in kobo (NGN) now means a currency column would need to be retrofitted — flagging so it's a conscious decision, not an oversight.

---

**Next step**: confirm the schema assumptions in Section 4 and the role model in Section 12.2, and I'll move to Phase 2 (domain package + migrations).
