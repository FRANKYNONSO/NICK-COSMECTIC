"""Domain-level exceptions. Infrastructure/API layers catch these and
translate them into HTTP responses, dialog boxes, etc. — the domain
layer itself never knows about HTTP status codes or UI concerns.
"""


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class InsufficientStockError(DomainError):
    def __init__(self, variant_id: str, requested: int, available: int):
        self.variant_id = variant_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for variant {variant_id}: "
            f"requested {requested}, available {available}"
        )


class InvalidRoleError(DomainError):
    def __init__(self, role: str):
        self.role = role
        super().__init__(f"'{role}' is not a recognized role")


class PermissionDeniedError(DomainError):
    def __init__(self, action: str, role: str):
        self.action = action
        self.role = role
        super().__init__(f"Role '{role}' is not permitted to perform '{action}'")


class DuplicateSaleError(DomainError):
    """Raised when a sale with the same local_ref_id already exists —
    the sync layer should treat this as a no-op, not an error, but the
    domain surfaces it so the caller can decide.
    """

    def __init__(self, local_ref_id: str):
        self.local_ref_id = local_ref_id
        super().__init__(f"Sale with local_ref_id '{local_ref_id}' already exists")


class EntityNotFoundError(DomainError):
    def __init__(self, entity_type: str, entity_id: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} with id '{entity_id}' not found")


class CouponNotFoundError(DomainError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Coupon '{code}' not found")


class CouponAlreadyUsedError(DomainError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Coupon '{code}' has already been redeemed")


class CouponVoidedError(DomainError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Coupon '{code}' has been voided")


class CouponExpiredError(DomainError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Coupon '{code}' expired")


class PaymentMismatchError(DomainError):
    """Raised when the tenders attached to a sale don't cover its
    total. Undertendering is always rejected; overtendering (e.g. cash
    handed over exceeds the total) is allowed — see Sale.change_due.
    """

    def __init__(self, total_kobo: int, tendered_kobo: int):
        self.total_kobo = total_kobo
        self.tendered_kobo = tendered_kobo
        super().__init__(
            f"Payment of {tendered_kobo} kobo does not cover total of {total_kobo} kobo"
        )


class SaleAlreadyVoidedError(DomainError):
    def __init__(self, sale_id: str):
        self.sale_id = sale_id
        super().__init__(f"Sale '{sale_id}' has already been voided")


class SaleAlreadySyncedError(DomainError):
    """Raised when attempting to void a sale that has already been
    pushed to the cloud. A synced sale requires a proper refund
    workflow (which reconciles server-side stock/financials too), not
    a local-only void.
    """

    def __init__(self, sale_id: str):
        self.sale_id = sale_id
        super().__init__(
            f"Sale '{sale_id}' has already synced and can't be voided locally; "
            "process a refund instead"
        )
