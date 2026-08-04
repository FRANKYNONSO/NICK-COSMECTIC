from fastapi import FastAPI

from api.v1.routers import auth

app = FastAPI(
    title="Nick Cosmetics API",
    version="0.1.0",
    description="Cloud API for the Nick Cosmetics retail platform.",
)

app.include_router(auth.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nick-cosmetics-api"}
