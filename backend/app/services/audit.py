"""AuditLog writer used by admin, pricing and gate overrides (FR-23, FR-27, FR-29)."""
import json

from sqlalchemy.orm import Session

from .. import clock
from ..models import AuditLog


def record(db: Session, user_id, action: str, entity: str, entity_id=None, old=None, new=None):
    def dump(v):
        if v is None or isinstance(v, str):
            return v
        return json.dumps(v, default=str)
    db.add(AuditLog(user_id=user_id, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id is not None else None,
                    old_value=dump(old), new_value=dump(new), created_at=clock.now()))
