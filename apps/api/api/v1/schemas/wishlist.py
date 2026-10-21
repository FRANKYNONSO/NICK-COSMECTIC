import uuid
from datetime import datetime

from pydantic import BaseModel


class WishlistAddRequest(BaseModel):
    product_id: uuid.UUID


class WishlistItemOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    product_image_url: str | None
    price_naira: float
    in_stock: bool
    created_at: datetime
