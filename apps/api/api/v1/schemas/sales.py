import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SaleLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(gt=0)
    unit_price_naira: float = Field(ge=0)


class PaymentTenderIn(BaseModel):
    method: str = Field(pattern="^(cash|card|transfer|online)$")
    amount_naira: float = Field(gt=0)


class CheckoutRequest(BaseModel):
    branch_id: uuid.UUID
    channel: str = Field(pattern="^(pos|website|mobile)$")
    local_ref_id: str = Field(min_length=1, max_length=200)
    items: list[SaleLineIn] = Field(min_length=1)
    customer_id: uuid.UUID | None = None
    discount_naira: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|card|transfer|online)$")
    # For a single-tender sale, payment_method above is enough and
    # payments can be omitted. For split tender (part cash, part
    # transfer, etc.), send payments instead -- entries must sum to at
    # least the sale total (subtotal minus discount), checked
    # server-side; tendering more than the total is allowed (e.g. cash
    # handed over is bigger than the total) and the excess becomes
    # change due, tracked separately rather than rejected. If both are
    # sent, payments takes precedence.
    payments: list[PaymentTenderIn] | None = None
    shift_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The open shift this sale is being rung up under. Required "
            "for channel=pos; omit for website/mobile channels, which "
            "don't use shifts."
        ),
    )


class SaleItemOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    stock_batch_id: uuid.UUID | None
    quantity: int
    unit_price_naira: float
    line_total_naira: float
    product_name: str | None = None
    unit_label: str | None = None


class SalePaymentOut(BaseModel):
    """One recorded tender against a sale/booking -- e.g. a single
    part-payment top-up. Every part-payment on a booking gets its own
    row here, so the full deposit/top-up/completion history (amount,
    method, who rang it up, when) is always visible, not just a
    running total.
    """

    id: uuid.UUID
    method: str
    amount_naira: float
    recorded_by: uuid.UUID | None = None
    recorded_by_name: str | None = None
    created_at: datetime | None = None


class RefundOut(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    processed_by: uuid.UUID | None
    amount_naira: float
    reason: str | None
    image_url: str | None = None
    action: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    status: str = "completed"
    exchange_product_id: uuid.UUID | None = None
    settlement: str | None = None
    settlement_amount_naira: float | None = None
    settlement_method: str | None = None
    created_at: datetime
    sale_status: str


class PendingChangeSummary(BaseModel):
    """Compact pending-change status embedded on SaleOut for the Sales/
    Transaction History table (spec section 2.5) -- the full record
    (bank/airtime detail etc.) lives on GET /sales/pending-changes,
    this is just enough to render a "Change: Pending / Resolved via
    X" indicator inline without a second round-trip.

    A sale can carry more than one of these -- change owed can be
    split across settlement methods (e.g. half handed over as cash on
    the spot, half as an airtime top-up), and each half is its own
    PendingChange row/reference code so it can be settled/approved
    independently. SaleOut.pending_changes is therefore a list, not a
    single optional record.
    """

    id: uuid.UUID
    status: str
    settlement_method: str | None = None
    reference_code: str | None = None
    amount_naira: float


class SaleOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    customer_id: uuid.UUID | None
    served_by: uuid.UUID | None
    cashier_name: str | None = None
    channel: str
    status: str
    subtotal_naira: float
    discount_naira: float
    total_naira: float
    payment_method: str | None
    local_ref_id: str | None
    # Free-text flexible pickup window for a booking (e.g. "later this
    # week", "by Friday") -- null for an ordinary completed sale.
    notes: str | None = None
    # Firm pickup date for a booking, if one was given (vs. the
    # free-text pickup_note above). Drives the 1-year deposit-storage
    # threshold.
    pickup_date: datetime | None = None
    # 1%/day storage/security-insurance charge, only nonzero once an
    # open booking has passed the 1-year threshold (see
    # _booking_storage_fee_kobo). Not included in total_naira -- it's
    # additive on top, same way the booking screen already treats
    # paid_naira as separate from total_naira.
    storage_fee_naira: float = 0
    # Sum of every SalePayment tender recorded against this sale so
    # far. For an ordinary completed sale this equals total_naira; for
    # an open booking or credit sale it can be less -- see
    # balance_naira for what's still owed.
    paid_naira: float = 0
    # What's still owed: (total_naira + storage_fee_naira) - paid_naira,
    # floored at 0. Added so credit sales and bookings can show a
    # running "amount remaining" on the receipt instead of just the
    # paid total -- previously there was no field for this at all, so
    # the receipt had nothing to display even once paid_naira was
    # correct.
    balance_naira: float = 0
    # Full part-payment history -- every deposit and top-up, each
    # tagged with the staff member who recorded it. Empty for a
    # simple single-tender sale that never went through a payments
    # row (payment_method covers that case instead).
    payments: list[SalePaymentOut] = []
    created_at: datetime
    items: list[SaleItemOut]
    # Customer on file for this sale, if any -- shown on the printable
    # receipt (spec: refund/booking receipts must include "Customer
    # information"). Null for a walk-in/anonymous sale.
    customer_name: str | None = None
    customer_phone: str | None = None
    # September 2026 change spec section 12: the same "View" receipt
    # for a sale must show its full accumulated history in place
    # (sold -> refunded -> remaining) rather than a separate
    # refund-specific receipt ever being generated. Empty for a sale
    # that's never had a refund processed against it.
    refunds: list[RefundOut] = []
    # September 2026 change spec section 9 -- the full original-item ->
    # swap -> new-item chain for a booking that's had its item changed,
    # so the transaction is traceable rather than only showing the
    # final state. Empty for a sale that's never had an item swapped.
    item_swap_history: list[dict] = []
    # Platform update spec section 2.5: "A completed sale that has a
    # pending or resolved change component must show that status in
    # the Sales/Transaction History, not only on the printed receipt."
    # Null for a sale that never had a change-owed record created
    # against it at all.
    pending_changes: list[PendingChangeSummary] = []


class RefundLineIn(BaseModel):
    sale_item_id: uuid.UUID
    quantity: int = Field(gt=0)
    restock: bool = True
    # Item came back damaged/unsellable -- forces restock off (see
    # validation in the endpoint) and instead logs a traceable
    # received-then-written-off batch at the warehouse branch, so it
    # shows up in that product's warehouse stock history rather than
    # silently vanishing.
    spoiled: bool = False


class RefundCreate(BaseModel):
    lines: list[RefundLineIn] = Field(min_length=1)
    # Required: refunds must be traceable to a stated reason, per store
    # policy -- no more silent/unexplained refunds.
    reason: str = Field(min_length=1, max_length=200)
    # Photo of the returned item. Required: a refund cannot be processed
    # without evidence of the returned/damaged item's condition.
    image_url: str = Field(min_length=1)
    # What the customer wants (spec section 7): a plain Refund is now
    # always paid out by bank transfer -- September 2026 change spec
    # section 10 removed 'cash' as a refund method system-wide, except
    # for the same-day booking-cancellation/change case, which has its
    # own dedicated endpoint logic (cancel_booking()/
    # change_booking_item()) and doesn't go through this schema at
    # all. 'cash' is intentionally still accepted by the pattern below
    # so the router can catch it and return a clear, specific error
    # instead of a generic validation failure.
    action: str = Field(default="transfer", pattern="^(cash|transfer|exchange)$")
    # Bank details (spec section 8) -- required when action='transfer'
    # (i.e. always, for a plain refund, now that cash is gone).
    # Also doubles as the payout destination when an exchange settles
    # 'store_paid_customer' via transfer instead of handing back cash
    # (settlement_method below).
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    exchange_product_id: uuid.UUID | None = None
    exchange_quantity: int = Field(default=1, gt=0)
    # Payment method for a customer top-up when the exchange item
    # costs more than the refunded amount. Ignored otherwise. (This is
    # the CUSTOMER paying the STORE more, not a refund -- section 10's
    # cash removal doesn't apply to it.)
    exchange_payment_method: str = Field(default="cash", pattern="^(cash|card|transfer|online)$")
    # How the store pays the customer back when the exchange item
    # costs LESS (settlement='store_paid_customer'): bank transfer only
    # now (section 10) -- 'cash' is still accepted by the pattern so
    # the router can return a clear, specific error rather than a
    # generic validation failure. Ignored unless the exchange actually
    # owes the customer money.
    settlement_method: str = Field(default="transfer", pattern="^(cash|transfer)$")


class RefundListItem(BaseModel):
    """Same shape as RefundOut, plus the display fields the Invoice
    page's Refunds section needs (who rang up the original sale, its
    receipt number/date) without a second round trip per row.
    """

    id: uuid.UUID
    sale_id: uuid.UUID
    receipt_number: str | None = None
    original_sale_at: datetime
    original_cashier_name: str | None = None
    processed_by: uuid.UUID | None
    processed_by_name: str | None = None
    amount_naira: float
    reason: str | None
    image_url: str | None = None
    action: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    status: str = "completed"
    approved_by_name: str | None = None
    processed_at: datetime | None = None
    settlement: str | None = None
    settlement_amount_naira: float | None = None
    settlement_method: str | None = None
    created_at: datetime
    # Which unified-history source this refund came from (spec section
    # 14: normal sale / credit sale / open booking, all logged into
    # one history) -- derived from the original Sale's own fields
    # rather than stored redundantly on Refund itself.
    source: str = "sale"


class RefundItemOut(BaseModel):
    product_name: str | None = None
    unit_label: str | None = None
    quantity: int
    unit_price_naira: float
    line_total_naira: float
    restocked: bool = True
    spoiled: bool = False


class RefundDetailOut(BaseModel):
    """Full record behind the printable refund receipt (spec: Refund →
    Details must show a proper receipt, not a plain-text summary).
    Everything here is pulled from the actual refund/sale/customer
    records -- nothing hardcoded or demo data.
    """

    id: uuid.UUID
    sale_id: uuid.UUID
    receipt_number: str | None = None
    branch_id: uuid.UUID
    original_sale_at: datetime
    original_cashier_name: str | None = None
    processed_by: uuid.UUID | None
    processed_by_name: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    items: list[RefundItemOut] = []
    amount_naira: float
    reason: str | None
    image_url: str | None = None
    action: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    status: str = "completed"
    approved_by_name: str | None = None
    processed_at: datetime | None = None
    exchange_product_id: uuid.UUID | None = None
    settlement: str | None = None
    settlement_amount_naira: float | None = None
    settlement_method: str | None = None
    original_payment_method: str | None = None
    created_at: datetime


class AuditLogOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    metadata: dict | None
    created_at: datetime


# --------------------------------------------------------------- BOOKINGS
#
# A booking is a Sale row with status="booking": stock is deducted
# up front (the same FIFO allocation an ordinary checkout uses -- see
# SalesService.checkout(status=...)), so the item is genuinely reserved,
# not just noted down. It carries a single SalePayment for the deposit,
# and a free-text `notes` field for the flexible pickup window, since
# the customer isn't held to an exact date/time.

class BookingCreateRequest(BaseModel):
    branch_id: uuid.UUID
    local_ref_id: str = Field(min_length=1, max_length=200)
    items: list[SaleLineIn] = Field(min_length=1)
    # A booking must be tied to an identifiable customer -- either an
    # existing customer_id, or their full legal name + phone number so
    # a new customer record can be created/matched from those (spec:
    # "it should be a must that you must take the customer's full
    # legal name and the phone number when trying to book"). Enforced
    # in the router below rather than here, since "customer_id OR
    # (name AND phone)" isn't expressible as a single field's
    # required-ness.
    customer_id: uuid.UUID | None = None
    customer_full_name: str | None = Field(default=None, max_length=200)
    customer_phone: str | None = Field(default=None, max_length=50)
    deposit_amount_naira: float = Field(gt=0)
    deposit_method: str = Field(pattern="^(cash|card|transfer|online)$")
    # Deliberately free text, not a date/time picker -- the customer
    # gave a rough window ("later this week", "by the 15th") and isn't
    # penalised for missing it exactly.
    pickup_note: str | None = Field(default=None, max_length=500)
    # A coupon's discount amount, resolved client-side the same way
    # CheckoutRequest.discount_naira is -- lets a booking be opened
    # with a discount applied, same as an ordinary sale.
    discount_naira: float = Field(default=0, ge=0)
    pickup_date: datetime | None = None


class BookingAddPaymentRequest(BaseModel):
    """A part-payment top-up against an open booking. amount_naira can
    be less than the remaining balance (another top-up can follow
    later) -- the booking only flips to completed once the running
    total of all payments reaches the sale total.
    """

    amount_naira: float = Field(gt=0)
    payment_method: str = Field(pattern="^(cash|card|transfer|online)$")
    # Optional -- lets the cashier set/update the pickup date at the
    # same time as recording a payment (e.g. once the customer
    # confirms a firm date), without a separate endpoint.
    pickup_date: datetime | None = None


class BookingCancelRequest(BaseModel):
    # Mandatory -- spec: "There must NOT be a simple one-click
    # cancellation... require a mandatory cancellation reason... Do
    # not allow cancellation with an empty reason." min_length=1 plus
    # the router below also rejects a whitespace-only string.
    reason: str = Field(min_length=1, max_length=500)
    # September 2026 change spec section 9: an open booking no longer
    # deducts stock at creation time, so there's nothing to restock on
    # cancellation anymore -- this field is a no-op, kept only for
    # backward compatibility with any existing caller that still sends
    # it.
    restock: bool = True
    # September 2026 change spec section 10: cancelling a booking with
    # a deposit already paid must trigger the same same-day-cash /
    # after-24h-bank-transfer refund logic as a Change Item (section
    # 9) -- the router decides which of these it actually requires.
    refund_method: str | None = Field(default=None, pattern="^(cash|transfer)$")
    refund_bank_name: str | None = Field(default=None, max_length=200)
    refund_account_number: str | None = Field(default=None, max_length=50)
    refund_account_name: str | None = Field(default=None, max_length=200)

class BookingChangeItemRequest(BaseModel):
    """Swap the single product on an open booking for a different one
    -- spec section 1: search/select another product, recalculate the
    total against the new item's price, keep the deposit already
    paid, and recompute the balance. Only supported for a booking with
    exactly one line item (the common case); a multi-line booking
    rejects this with a clear error rather than guessing which line to
    swap.
    """

    product_id: uuid.UUID
    quantity: int = Field(gt=0, default=1)
    # If omitted, the variant's current selling price is used
    # (spec: "automatically retrieve the current selling price").
    unit_price_naira: float | None = Field(default=None, ge=0)
    # September 2026 change spec section 9: only needed when the new
    # item costs less than the deposit already paid -- the router
    # decides (same-day + cash-paid vs. everything else) which of
    # these it actually requires, mirroring section 10's cancellation
    # refund logic.
    refund_method: str | None = Field(default=None, pattern="^(cash|transfer)$")
    refund_bank_name: str | None = Field(default=None, max_length=200)
    refund_account_number: str | None = Field(default=None, max_length=50)
    refund_account_name: str | None = Field(default=None, max_length=200)


class RefundMarkProcessed(BaseModel):
    """Super Admin / Branch Manager confirming a pending transfer
    refund has actually been paid (spec section 10)."""

    note: str | None = Field(default=None, max_length=500)


class PendingRefundSummary(BaseModel):
    """Dashboard badge count for pending refunds (spec section 10),
    same shape as the low-stock/expiry alert counts."""

    count: int


class PendingChangeCreate(BaseModel):
    """Platform update spec section 2 -- recorded against the Sale
    itself (sale_id), never a bare name string, so two customers
    sharing a name never collide. customer_name is still required so
    staff at the till have something to recognize/call out; phone is
    optional, purely a secondary lookup aid -- the actual unique
    identifier (spec 2.2) is the system-generated reference_code the
    server assigns on creation, always, regardless of method.

    settlement_method drives which detail fields are required (spec
    2.3):
      - 'cash': till doesn't have correct change on hand -- no detail
        fields required, just the reference code so the customer can
        be matched when they return.
      - 'transfer': bank_name/account_number/account_name required.
      - 'airtime': phone_number required; network is optional --
        auto-detected server-side from the Nigerian prefix when
        omitted, the cashier only needs to confirm/override it.
    """

    sale_id: uuid.UUID
    customer_name: str = Field(min_length=1, max_length=200)
    customer_phone: str | None = Field(default=None, max_length=32)
    amount_naira: float = Field(gt=0)
    notes: str | None = Field(default=None, max_length=500)
    settlement_method: str = Field(default="cash", pattern="^(cash|transfer|airtime)$")
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    airtime_phone_number: str | None = Field(default=None, max_length=32)
    airtime_network: str | None = Field(default=None, max_length=32)


class PendingChangeSettle(BaseModel):
    """Cashier confirming exactly how an *outstanding* (cash-tracked)
    pending change was actually handed back once the customer
    returned for it. Amount and method are reconfirmed here rather
    than assumed from how the record was first logged, since the
    customer may ask for a different method today than what was
    recorded at the till (e.g. logged as cash-owed, but they'd rather
    have a transfer now).

    Choosing 'cash' here settles the record immediately, same as
    before. Choosing 'transfer'/'airtime' routes it into the same
    Pending Approval queue a transfer/airtime request gets at
    creation (spec 2.4) -- it's real money moving, so a Branch
    Manager/Super Admin still has to confirm it was actually sent,
    even though it started life as a cash record.
    """

    amount_naira: float = Field(gt=0)
    settlement_method: str = Field(pattern="^(cash|transfer|airtime)$")
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    airtime_phone_number: str | None = Field(default=None, max_length=32)
    airtime_network: str | None = Field(default=None, max_length=32)


class PendingChangeOut(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    customer_name: str
    customer_phone: str | None = None
    amount_naira: float
    notes: str | None = None
    # 'outstanding' (cash, waiting for the customer to return),
    # 'pending' (transfer/airtime submitted, awaiting Super Admin/
    # Branch Manager approval -- spec 2.4), or 'settled' (done).
    status: str
    reference_code: str | None = None
    settlement_method: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    airtime_phone_number: str | None = None
    airtime_network: str | None = None
    created_by: uuid.UUID | None = None
    created_by_name: str | None = None
    created_at: datetime
    settled_by: uuid.UUID | None = None
    settled_by_name: str | None = None
    settled_at: datetime | None = None
    # Filled once a super_admin/branch_manager has reviewed a pending
    # transfer/airtime request -- same "seen it, actioned it" trail as
    # Refund.approved_by/processed_at.
    approved_by: uuid.UUID | None = None
    approved_by_name: str | None = None
    processed_at: datetime | None = None


class PendingChangeMarkProcessed(BaseModel):
    """Super Admin / Branch Manager confirming a pending transfer/
    airtime change settlement has actually been sent (spec 2.4),
    mirroring RefundMarkProcessed."""

    note: str | None = Field(default=None, max_length=500)