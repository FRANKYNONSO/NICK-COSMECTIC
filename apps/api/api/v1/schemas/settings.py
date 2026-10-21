from pydantic import BaseModel, Field


class StoreSettingsOut(BaseModel):
    store_name: str
    store_email: str | None = None
    logo_url: str | None = None
    # Spec: the receipt QR code is NOT for viewing an invoice online --
    # it's a fixed link to the store's own online shop, so a customer
    # can scan it to go buy from there. `website_qr_caption` is the
    # optional line printed above the code (e.g. "Visit our online
    # store"). Both are freely editable by the super admin at any time,
    # not a one-time setup step.
    website_url: str | None = None
    website_qr_caption: str | None = None

    model_config = {"from_attributes": True}


class StoreSettingsUpdate(BaseModel):
    store_name: str | None = Field(default=None, max_length=200)
    store_email: str | None = Field(default=None, max_length=200)
    website_url: str | None = Field(default=None, max_length=300)
    website_qr_caption: str | None = Field(default=None, max_length=200)
