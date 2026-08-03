FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg/bcrypt build steps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the shared core package first so the editable install resolves
COPY packages/core /app/packages/core

COPY apps/api/requirements.txt .
RUN sed -i 's#-e ../../packages/core#-e /app/packages/core#' requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--loop", "asyncio"]
