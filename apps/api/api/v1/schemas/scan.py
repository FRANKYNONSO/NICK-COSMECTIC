import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ScanSessionOut(BaseModel):
    code: str
    branch_id: uuid.UUID | None
    expires_at: datetime

    model_config = {"from_attributes": True}


class ScanSubmitRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=200)


class ScanPollOut(BaseModel):
    code: str
    barcode: str | None
    scanned_at: datetime | None
