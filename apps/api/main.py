from fastapi import FastAPI

app = FastAPI(
    title="Nick Cosmetics API",
    version="0.1.0",
    description="Cloud API for the Nick Cosmetics retail platform.",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nick-cosmetics-api"}
