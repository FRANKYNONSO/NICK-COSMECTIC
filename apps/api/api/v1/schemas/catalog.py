import uuid

from pydantic import BaseModel, Field


# ------------------------------------------------------------- BRANDS

class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


# --------------------------------------------------------- CATEGORIES

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None

    model_config = {"from_attributes": True}


# ----------------------------------------------------------- PRODUCTS

class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    barcode: str | None = None
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    brand_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    unit_of_measure: str = "each"


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    brand_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(active|discontinued|draft)$")


class ProductOut(BaseModel):
    id: uuid.UUID
    sku: str
    barcode: str | None
    name: str
    description: str | None
    brand_id: uuid.UUID | None
    category_id: uuid.UUID | None
    status: str
    unit_of_measure: str

    model_config = {"from_attributes": True}


# ------------------------------------------------------------ VARIANTS
# Prices are accepted/returned in Naira (human-friendly) at the API
# boundary and converted to/from integer kobo internally via
# nickcosmetics_core.domain.money.Money -- see api/v1/routers/catalog.py.

class ProductVariantCreate(BaseModel):
    variant_name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=100)
    barcode: str | None = None
    cost_price_naira: float = Field(ge=0)
    selling_price_naira: float = Field(ge=0)
    reorder_level: int = Field(default=5, ge=0)


class ProductVariantOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    variant_name: str
    sku: str
    barcode: str | None
    cost_price_naira: float
    selling_price_naira: float
    reorder_level: int

    model_config = {"from_attributes": True}
