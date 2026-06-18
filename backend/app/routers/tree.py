"""Tree navigation endpoints (lazy, one level at a time)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Node
from ..schemas import TreeChildrenOut
from ..serializers import build_node_outs

router = APIRouter(prefix="/api/tree", tags=["tree"])


@router.get("/children", response_model=TreeChildrenOut)
def children(
    dataset_id: int = Query(...),
    parent_id: int | None = Query(None),
    types: list[str] | None = Query(None),
    accessed_after: date | None = Query(None),
    accessed_before: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """Direct children of ``parent_id`` (or dataset roots when parent_id is None).

    Folder children carry ``filtered_file_count`` / ``filtered_total_size`` which
    honour the type and last-accessed filters.
    """
    stmt = select(Node).where(Node.dataset_id == dataset_id)
    if parent_id is None:
        stmt = stmt.where(Node.parent_id.is_(None))
    else:
        if not db.get(Node, parent_id):
            raise HTTPException(status_code=404, detail="Parent node not found")
        stmt = stmt.where(Node.parent_id == parent_id)
    stmt = stmt.order_by(Node.is_dir.desc(), func.lower(Node.name))

    nodes = list(db.execute(stmt).scalars().all())
    items = build_node_outs(
        db, nodes, types=types, accessed_after=accessed_after,
        accessed_before=accessed_before,
    )
    return TreeChildrenOut(parent_id=parent_id, children=items)
