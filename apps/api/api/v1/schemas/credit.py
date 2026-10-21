import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreditCreate(BaseModel):
    branch_id: uuid.UUID
    customer_full_name: str = Field(min_length=1, max_length=200)
    customer_phone: str = Field(min_length=1, max_length=50)
    # Optional per spec ("if possible, but it should be optional, their
    # address, their home address too").
    customer_address: str | None = None
    # What they're buying -- required, and pulled straight out of the
    # catalog (spec: "everything in the option of what the person is
    # buying should be in direct reflection of what is on the
    # inventory"). category_id is kept mainly to drive the frontend's
    # cascading picker; product_id is what actually prices and
    # identifies the item.
    category_id: uuid.UUID | None = None
    product_id: uuid.UUID
    quantity: int = Field(gt=0, default=1)
    description: str | None = Field(default=None, max_length=500)
    # Total amount owed. The frontend auto-fills this from the
    # product's selling price * quantity; the server independently
    # re-derives the same total from the product's current selling
    # price and quantity and uses THAT as the source of truth (see
    # create_credit) -- a client-supplied amount_naira is only ever
    # used as an audit note if it disagrees, never trusted outright,
    # per spec: "the user should not manually calculate the balance...
    # backend must validate the calculation."
    amount_naira: float = Field(gt=0)
    # Optional deposit taken at the moment the credit is opened (spec
    # example: "Amount paid = ₦50,000"). Recorded as an ordinary
    # CreditPayment row, same as any later top-up. Defaults to 0 (pure
    # credit, nothing paid yet).
    amount_paid_naira: float = Field(default=0, ge=0)
    payment_method: str = Field(default="cash", pattern="^(cash|card|transfer)$")
    # A coupon's discount amount, applied against this single credit
    # line's total -- coupons on Open Credit apply to one line only
    # (whichever the frontend chooses to absorb it), since each line
    # is its own Sale record and a coupon can only be redeemed against
    # one Sale.
    discount_naira: float = Field(default=0, ge=0)


class CreditPaymentCreate(BaseModel):
    amount_naira: float = Field(gt=0)
    method: str = Field(default="cash", pattern="^(cash|card|transfer)$")


class CreditPaymentOut(BaseModel):
    id: uuid.UUID
    amount_naira: float
    method: str
    received_by_name: str
    paid_at: datetime

    model_config = {"from_attributes": True}


class CreditOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    customer_full_name: str
    customer_phone: str
    customer_address: str | None
    description: str | None
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    name: str | None = None
    quantity: int | None = None
    unit_price_naira: float | None = None
    amount_naira: float
    amount_paid_naira: float
    balance_naira: float
    status: str
    sale_id: uuid.UUID | None
    opened_by_name: str
    # Date AND time the credit was opened, per spec.
    opened_at: datetime
    payments: list[CreditPaymentOut] = []

    model_config = {"from_attributes": True}
