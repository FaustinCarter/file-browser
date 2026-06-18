"""Tree navigation endpoints (lazy, one level at a time)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .. import services
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
    no_transfer: str | None = Query(None, description="'yes' (only marked) / 'no' (hide marked)"),
    processed: str | None = Query(None, description="'yes' (only marked) / 'no' (hide marked)"),
    jira: str | None = Query(None, description="effective JIRA value, or '__none__' for unassigned"),
    assignee: str | None = Query(None, description="effective assignee, or '__none__' for unassigned"),
    db: Session = Depends(get_db),
):
    """Direct children of ``parent_id`` (or dataset roots when parent_id is None).

    When any filter is active, non-matching file rows are hidden and folders with
    no remaining visible files drop out; folder rows carry rollup counts so the UI
    can show tri-state No-Transfer / Processed checkboxes.
    """
    if parent_id is not None and not db.get(Node, parent_id):
        raise HTTPException(status_code=404, detail="Parent node not found")

    f = services.build_filters(
        db, dataset_id, types=types, accessed_after=accessed_after,
        accessed_before=accessed_before, no_transfer=no_transfer, processed=processed,
        jira=jira, assignee=assignee,
    )

    parent_cond = (
        Node.parent_id.is_(None) if parent_id is None else Node.parent_id == parent_id
    )

    # Folders: keep them all for now (filtered out below if they have no visible
    # files). Files: apply the view filter so non-matching rows disappear.
    folders = list(
        db.execute(
            select(Node)
            .where(Node.dataset_id == dataset_id, parent_cond, Node.is_dir.is_(True))
            .order_by(func.lower(Node.name))
        ).scalars().all()
    )
    file_conds = [Node.dataset_id == dataset_id, parent_cond, Node.is_dir.is_(False)]
    if f["view_filter"] is not None:
        file_conds.append(f["view_filter"])
    files = list(
        db.execute(
            select(Node).where(and_(*file_conds)).order_by(func.lower(Node.name))
        ).scalars().all()
    )

    folder_outs = build_node_outs(
        db, folders, view_filter=f["view_filter"], nt_clause=f["nt_clause"],
        proc_clause=f["proc_clause"],
    )
    if f["filter_active"]:
        folder_outs = [fo for fo in folder_outs if (fo.filtered_file_count or 0) > 0]

    file_outs = build_node_outs(db, files, with_folder_stats=False)

    return TreeChildrenOut(parent_id=parent_id, children=folder_outs + file_outs)
