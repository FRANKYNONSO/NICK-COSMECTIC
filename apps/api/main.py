from dotenv import load_dotenv
load_dotenv()

import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from api.v1.routers import (
    auth,
    branches,
    catalog,
    chat,
    coupons,
    credit,
    customer_auth,
    customers,
    inventory,
    notifications,
    password_reset_requests,
    product_templates,
    profile,
    purchase_invoices,
    purchasing,
    reports,
    restock_list,
    roles,
    sales,
    scan,
    settings,
    shifts,
    staff_applications,
    statements,
    storefront_orders,
    users,
    warehouse,
    wishlist,
)
from tasks.metric_snapshots import run_daily_metric_rollup

app = FastAPI(
    title="Nick Cosmetics API",
    version="0.1.0",
    description="Cloud API for the Nick Cosmetics retail platform.",
)

# Allows the web admin portal (served locally on port 5500) to call this
# API from the browser. Add any other local dev origins here as needed.
# Restrict this to your real admin domain once the portal is deployed
# somewhere other than localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://127.0.0.1:5500', 'http://localhost:5500', 'http://127.0.0.1:8000', 'http://localhost:8000'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Explicit catch-all OPTIONS handler. Starlette's CORSMiddleware normally
# intercepts preflight OPTIONS requests automatically, but some setups
# (e.g. the admin portal loaded from a file:// URL) don't trigger that
# path reliably, causing FastAPI to route OPTIONS to the endpoint itself
# and return 405. This guarantees every OPTIONS request gets a clean 200,
# with CORSMiddleware still attaching the required Access-Control-* headers.
@app.options("/{full_path:path}")
async def preflight_handler(full_path: str, request: Request) -> Response:
    return Response(status_code=200)


os.makedirs("static/uploads/products", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router, prefix="/api/v1")
app.include_router(password_reset_requests.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(roles.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(customer_auth.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(branches.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(sales.router, prefix="/api/v1")
app.include_router(coupons.router, prefix="/api/v1")
app.include_router(credit.router, prefix="/api/v1")
app.include_router(purchasing.router, prefix="/api/v1")
app.include_router(purchase_invoices.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(statements.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(restock_list.router, prefix="/api/v1")
app.include_router(shifts.router, prefix="/api/v1")
app.include_router(warehouse.router, prefix="/api/v1")
app.include_router(product_templates.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(scan.router, prefix="/api/v1")
app.include_router(staff_applications.router, prefix="/api/v1")
app.include_router(wishlist.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(storefront_orders.router, prefix="/api/v1")
from api.v1.routers import goods_receiving  # noqa: E402
app.include_router(goods_receiving.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nick-cosmetics-api"}


# Dashboard redesign implementation prompt, step 4: a daily rollup job
# feeding Performance Records. No pre-existing background-job
# scheduler exists anywhere in this project (no celery beat schedule,
# no other cron-style setup) to hang this off of instead, so this adds
# APScheduler fresh, registered in the FastAPI startup event as the
# implementation prompt calls for. Runs once daily just after
# midnight; misfire_grace_time tolerates the process having been down
# at the scheduled minute without skipping that day's run entirely.
_scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def _start_metric_snapshot_scheduler() -> None:
    _scheduler.add_job(
        run_daily_metric_rollup,
        trigger="cron",
        hour=0,
        minute=5,
        id="daily_metric_snapshot_rollup",
        misfire_grace_time=3600,
    )
    _scheduler.start()

