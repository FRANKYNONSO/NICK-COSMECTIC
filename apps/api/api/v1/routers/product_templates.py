"""Update Request item 8: "Product Categories" reworked into a catalog
of product-name + category + size templates that don't need to exist
in live inventory yet. See ProductTemplate/ProductTemplateCategory in
infrastructure/db/models.py and the schemas module for the reasoning.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.product_templates import (
    TEMPLATE_SIZE_CHOICES,
    ProductTemplateCategoriesUpdate,
    ProductTemplateCategoryOut,
    ProductTemplateCreate,
    ProductTemplateOut,
    ProductTemplateSuggestion,
    ProductTemplateSummaryOut,
)
from infrastructure.db.models import Category, Product
from infrastructure.db.models import ProductTemplate as Template
from infrastructure.db.models import ProductTemplateCategory as TemplateCategory
from infrastructure.db.models import User

router = APIRouter(prefix="/product-templates", tags=["product-templates"])


def _validate_sizes(sizes: list[str]) -> None:
    bad = [s for s in sizes if s not in TEMPLATE_SIZE_CHOICES]
    if bad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown size(s) {bad} -- choose from {list(TEMPLATE_SIZE_CHOICES)}.",
        )


async def _to_out(template: Template, session: AsyncSession) -> ProductTemplateOut:
    result = await session.execute(
        select(TemplateCategory, Category.name)
        .join(Category, TemplateCategory.category_id == Category.id)
        .where(TemplateCategory.template_id == template.id)
        .order_by(Category.name)
    )
    categories = [
        ProductTemplateCategoryOut(
            category_id=tc.category_id,
            category_name=cat_name,
            sizes=tc.sizes or [],
            byproduct_variants=tc.byproduct_variants or [],
        )
        for tc, cat_name in result.all()
    ]
    return ProductTemplateOut(id=template.id, name=template.name, created_at=template.created_at, categories=categories)


@router.get("", response_model=list[ProductTemplateSummaryOut])
async def list_templates(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[ProductTemplateSummaryOut]:
    result = await session.execute(
        select(Template.id, Template.name, Template.created_at, func.count(TemplateCategory.id))
        .outerjoin(TemplateCategory, TemplateCategory.template_id == Template.id)
        .group_by(Template.id)
        .order_by(Template.name)
    )
    return [
        ProductTemplateSummaryOut(id=tid, name=name, created_at=created_at, category_count=count)
        for tid, name, created_at, count in result.all()
    ]


@router.get("/suggest", response_model=list[ProductTemplateSuggestion])
async def suggest_templates(
    q: str = "",
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[ProductTemplateSuggestion]:
    """Backs Add Product's name typeahead -- a template match offers to
    auto-fill category + size options the same way an exact live-
    product match offers to open that product for editing instead.
    """
    if not q.strip():
        return []
    result = await session.execute(
        select(Template)
        .where(Template.name.ilike(f"%{q.strip()}%"))
        .options(selectinload(Template.categories).selectinload(TemplateCategory.category))
        .order_by(Template.name)
        .limit(8)
    )
    out = []
    for template in result.scalars().all():
        primary = template.categories[0] if template.categories else None
        out.append(
            ProductTemplateSuggestion(
                id=template.id,
                name=template.name,
                primary_category_id=primary.category_id if primary else None,
                primary_category_name=primary.category.name if primary else None,
                sizes=primary.sizes if primary else [],
            )
        )
    return out


@router.post("", response_model=ProductTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: ProductTemplateCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> ProductTemplateOut:
    name = body.name.strip()
    existing = await session.execute(select(Template).where(func.lower(Template.name) == name.lower()))
    template = existing.scalar_one_or_none()
    if template is None:
        template = Template(name=name)
        session.add(template)
        await session.commit()
        await session.refresh(template)
    return await _to_out(template, session)


@router.get("/{template_id}", response_model=ProductTemplateOut)
async def get_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> ProductTemplateOut:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product template not found")
    return await _to_out(template, session)


@router.put("/{template_id}/categories", response_model=ProductTemplateOut)
async def set_template_categories(
    template_id: uuid.UUID,
    body: ProductTemplateCategoriesUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> ProductTemplateOut:
    """Replaces the template's entire set of category attachments in
    one call (see ProductTemplateCategoriesUpdate for why)."""
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product template not found")

    seen_categories: set[uuid.UUID] = set()
    for cfg in body.categories:
        if cfg.category_id in seen_categories:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Each category can only be attached once per template.")
        seen_categories.add(cfg.category_id)
        _validate_sizes(cfg.sizes)
        category = await session.get(Category, cfg.category_id)
        if category is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Category {cfg.category_id} not found")

    await session.execute(
        TemplateCategory.__table__.delete().where(TemplateCategory.template_id == template_id)
    )
    for cfg in body.categories:
        session.add(
            TemplateCategory(
                template_id=template_id,
                category_id=cfg.category_id,
                sizes=cfg.sizes,
                byproduct_variants=cfg.byproduct_variants,
            )
        )
    await session.commit()
    return await _to_out(template, session)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> None:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product template not found")
    await session.delete(template)
    await session.commit()
