"""Writes to the audit_logs table.

The AuditLog model has existed in the schema for a while but nothing
actually wrote to it -- this is the one place that does, so every
caller stays consistent (same field meanings, same commit behavior).

Callers add the entry to the session but don't commit -- it's meant
to ride along in the same transaction/commit as the action it's
logging (a refund, a discount), so the log can never exist without
the action it describes actually having happened, or vice versa.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import AuditLog


def log_audit_event(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    metadata: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            log_metadata=metadata,
        )
    )