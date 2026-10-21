"""Update Request item 8: schemas backing the reworked "Product
Categories" page -- a catalog of product-name + category + size
templates that don't have to exist in live inventory yet. See
ProductTemplate/ProductTemplateCategory in infrastructure/db/models.py
for why this is deliberately not the old retired variant system.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# The four fixed size choices the template configurator offers --
# Standard is new here (Add Product's own Size field still also
# allows free text via "quick add size", see catalog.py's relaxed
# _SIZE_PATTERN).
TEMPLATE_SIZE_CHOICES = ("Big", "Medium", "Small", "Standard")


class ProductTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProductTemplateCategoryConfig(BaseModel):
    category_id: uuid.UUID
    sizes: list[str] = Field(default_factory=list)
    byproduct_variants: list[str] = Field(default_factory=list)


class ProductTemplateCategoriesUpdate(BaseModel):
    """Replaces the *entire* set of category attachments for one
    template in a single call -- simpler for the configurator UI
    (which shows/edits the whole list at once) than a piecemeal
    add/remove API, at the cost of always sending the full list back.
    """

    categories: list[ProductTemplateCategoryConfig]


class ProductTemplateCategoryOut(BaseModel):
    category_id: uuid.UUID
    category_name: str
    sizes: list[str]
    byproduct_variants: list[str]

    model_config = {"from_attributes": True}


class ProductTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    categories: list[ProductTemplateCategoryOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ProductTemplateSummaryOut(BaseModel):
    """Lightweight row for the templates list table -- avoids loading
    every category config just to render a count."""

    id: uuid.UUID
    name: str
    category_count: int
    created_at: datetime


class ProductTemplateSuggestion(BaseModel):
    """What Add Product's name typeahead offers when a typed name
    matches a template -- enough to auto-fill category and size
    options without a second round trip.
    """

    id: uuid.UUID
    name: str
    primary_category_id: uuid.UUID | None = None
    primary_category_name: str | None = None
    sizes: list[str] = Field(default_factory=list)
