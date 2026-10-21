import uuid
from datetime import datetime

from pydantic import BaseModel, Field

UNIT_LABELS = ("pieces", "dozens", "cartons", "bags", "half_dozen", "half_carton")


class RestockEntrySendOut(BaseModel):
    id: uuid.UUID
    supplier_name: str
    quantity_sent: int
    unit_label_sent: str
    sent_by_name: str | None
    sent_at: datetime

    model_config = {"from_attributes": True}


class RestockListRowOut(BaseModel):
    """One row on the Restock List screen. Two shapes share this model:
    a catalog-linked row (merges a live low-stock/out-of-stock
    computation with any persisted RestockListEntry state), or a
    freeform row typed straight onto the list (product_id null,
    product_name/image_url come from custom_name/custom_image_url
    instead). `entry_id` is null only for a catalog row nobody has
    touched yet -- the frontend calls POST /restock-list/items the
    first time the person edits it, same lazy-creation pattern the
    entry model docstring describes; a freeform row always has an
    entry_id since there's nothing to lazily create it from.
    """

    entry_id: uuid.UUID | None
    product_id: uuid.UUID | None
    product_name: str
    category_name: str | None
    image_url: str | None
    quantity_available: int | None = None
    reorder_level: int | None = None
    reason: str = Field(description="'low_stock' | 'out_of_stock' | 'manual' | 'custom'")
    quantity: int
    unit_label: str
    sends: list[RestockEntrySendOut]


class RestockItemAdd(BaseModel):
    # Exactly one of these two identities must be given -- see
    # RestockListEntry's check constraint in models.py.
    product_id: uuid.UUID | None = None
    custom_name: str | None = Field(default=None, max_length=200)
    custom_image_url: str | None = None
    branch_id: uuid.UUID
    quantity: int = 1
    unit_label: str = "pieces"
    # True from the Inventory Catalog's "Add to restock list" button --
    # keeps this entry on the list regardless of stock level. False (the
    # default) is used when the frontend is just persisting a quantity/
    # unit edit on an item that's already showing up because it's low
    # or out of stock -- that kind of entry stays auto-removable.
    # Freeform (custom_name) items ignore this -- they're always
    # manual, there's no stock level to auto-remove them by.
    is_manual: bool = False


class RestockItemUpdate(BaseModel):
    quantity: int | None = None
    unit_label: str | None = None


class RestockDownloadRequest(BaseModel):
    entry_ids: list[uuid.UUID]
    # Optional per the person's explicit correction: a supplier name
    # is a nice-to-have on the PDF, not a requirement to download it.
    # When given and it doesn't match an existing Supplier (see
    # purchasing.py), the router creates one -- so typing a new name
    # here is how a brand-new supplier gets onto the shared registry
    # that the Invoice page's supplier picker also reads from.
    supplier_name: str | None = None
