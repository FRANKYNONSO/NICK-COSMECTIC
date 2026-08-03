# Nick Cosmetics — Retail Management Platform

Python-only retail platform: offline-first desktop POS, web admin portal,
e-commerce site, customer portal, mobile app, and cloud sync — all sharing
one PostgreSQL source of truth. See `docs/architecture/` for the full
Phase 1 architecture document.

## Repo layout

```
packages/core/     Shared domain + application logic (nickcosmetics_core)
                    — framework-agnostic, used by both the cloud API and
                    the offline desktop app.
apps/api/           Cloud FastAPI application.
apps/desktop_pos/   Offline-first PySide6 POS (scaffolded in a later phase).
apps/web_admin/     Web admin portal (later phase).
apps/ecommerce/     Public storefront + customer portal (later phase).
apps/mobile/        Staff/manager mobile app (later phase).
infra/docker/       Dockerfiles + docker-compose for local dev.
docs/               Architecture docs and ADRs.
```

## Local development (Docker Compose)

Requires Docker and Docker Compose.

```bash
cd infra/docker
docker compose -f docker-compose.dev.yml up --build
```

This starts:
- **Postgres 16** on `localhost:5432` (user/pass/db: `nickcosmetics`)
- **Redis 7** on `localhost:6379`
- **API** on `localhost:8000`, hot-reloading on code changes

Check it's alive:

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "nick-cosmetics-api"}
```

Interactive API docs: http://localhost:8000/docs

Stop everything:

```bash
docker compose -f docker-compose.dev.yml down
```

Add `-v` to also wipe the Postgres volume (fresh DB next boot).

## Running database migrations

The schema lives in `apps/api/alembic/versions/`, applied with Alembic —
not auto-created on startup (that dev shortcut has been removed now
that real migrations exist).

With the containers running (`docker compose up` in another terminal):

```bash
docker compose -f infra/docker/docker-compose.dev.yml exec api alembic upgrade head
```

This creates all tables (branches, users, catalog, inventory, sales,
etc. — see `docs/architecture/phase1-architecture.md` Section 4 for
the full schema). Check it worked:

```bash
docker compose -f infra/docker/docker-compose.dev.yml exec postgres psql -U nickcosmetics -d nickcosmetics -c "\dt"
```

You should see a list of ~19 tables.

To create a new migration after changing `infrastructure/db/models.py`:

```bash
docker compose -f infra/docker/docker-compose.dev.yml exec api alembic revision --autogenerate -m "describe your change"
docker compose -f infra/docker/docker-compose.dev.yml exec api alembic upgrade head
```

## Running the core package's tests

The shared domain package (`packages/core`) has no infrastructure
dependencies, so its tests run fast and don't need Docker at all:

```bash
cd packages/core
pip install -e ".[dev]"
pytest
```

This covers FIFO stock allocation, idempotent checkout (the mechanism
that makes offline sync safe), and role-based permission checks.

## Running the API outside Docker (optional)

If you'd rather run the API on your host machine against the Compose
Postgres/Redis:

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
cp .env.example .env
pip install -r requirements.txt
uvicorn main:app --reload
```

## What's implemented so far (Phase 2, in progress)

- [x] `nickcosmetics_core`: Money value object, User/Role + permissions,
      ProductVariant, StockBatch, FIFO allocator, Sale aggregate,
      SalesService checkout use case (idempotent, FIFO-aware)
- [x] Repository interfaces (`interfaces/repositories.py`)
- [x] Docker Compose dev environment (Postgres + Redis + API)
- [x] FastAPI app skeleton with `/health` endpoint
- [x] Full SQLAlchemy schema (19 tables — catalog, inventory, purchasing,
      sales, customers, audit, sync) + first Alembic migration
- [ ] Auth endpoints (JWT issuance, login, refresh)
- [ ] Catalog/inventory/sales API routers wired to the core package
- [ ] SQLAlchemy repository implementations satisfying `interfaces/repositories.py`

See `docs/architecture/` for the complete roadmap (Phases 2–8).
