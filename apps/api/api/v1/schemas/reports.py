import uuid
from datetime import date

from pydantic import BaseModel


class SalesSummaryRow(BaseModel):
    period: date
    sales_count: int
    subtotal_naira: float
    discount_naira: float
    total_naira: float


class SalesSummaryResponse(BaseModel):
    rows: list[SalesSummaryRow]
    grand_total_naira: float
    grand_sales_count: int


class LowStockRow(BaseModel):
    variant_id: uuid.UUID
    product_name: str
    variant_name: str
    sku: str
    quantity_available: int
    reorder_level: int


class ExpiryRow(BaseModel):
    stock_batch_id: uuid.UUID
    variant_id: uuid.UUID
    product_name: str
    variant_name: str
    sku: str
    quantity_remaining: int
    expiry_date: date
    days_until_expiry: int


class ProductPerformanceRow(BaseModel):
    variant_id: uuid.UUID
    product_name: str
    variant_name: str
    sku: str
    quantity_sold: int
    revenue_naira: float
