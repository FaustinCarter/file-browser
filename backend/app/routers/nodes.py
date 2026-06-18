"""Node detail, aggregates, search grid, and annotation editing."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, not_, select
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..models import Annotation, Node
from ..schemas import (
    AnnotationUpdate,
    BulkAnnotationUpdate,
    CountsOut,
    FolderFlagUpdate,
    FolderStatsOut,
    FolderTypeCountRequest,
    NodeOut,
    TypeBreakdownRow,
)
from ..serializers import build_node_outs

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

_SORT_COLUMNS = {
    "name": Node.name,
    "full_path": Node.full_path,
    "size": Node.size_bytes,
    "last_modified": Node.last_modified,
    "last_accessed": Node.last_accessed,
    "file_type": Node.file_type,
    "owner": Node.owner,
    "dir_level": Node.dir_level,
}


@router.get("/search")
def search(
    dataset_id: int = Query(...),
    q: str | None = Query(None, description="substring match on path/name"),
    types: list[str] | None = Query(None),
    owner: str | None = Query(None),
    is_dir: bool | None = Query(None),
    jira: str | None = Query(None),
    processed: str | None = Query(None, description="'yes' (marked) / 'no' (unmarked)"),
    no_transfer: str | None = Query(None, description="'yes' (marked) / 'no' (unmarked)"),
    accessed_after: date | None = Query(None),
    accessed_before: date | None = Query(None),
    under_node_id: int | None = Query(None, description="restrict to a subtree"),
    sort: str = Query("full_path"),
    direction: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Flat, paginated, filterable grid view across the dataset.

    The processed / no_transfer filters match a node's **effective** value
    (own or inherited from a parent folder). The jira filter matches own values.
    """
    f = services.build_filters(
        db, dataset_id, no_transfer=no_transfer, processed=processed,
    )
    conds = [Node.dataset_id == dataset_id]
    if q:
        conds.append(Node.full_path.ilike(f"%{q}%"))
    if types:
        conds.append(Node.file_type.in_(types))
    if owner:
        conds.append(Node.owner == owner)
    if is_dir is not None:
        conds.append(Node.is_dir.is_(is_dir))
    if accessed_after:
        conds.append(Node.last_accessed >= accessed_after)
    if accessed_before:
        conds.append(Node.last_accessed <= accessed_before)
    if under_node_id is not None:
        parent = db.get(Node, under_node_id)
        if not parent:
            raise HTTPException(status_code=404, detail="under_node_id not found")
        conds.append(Node.mat_path.like(f"{parent.mat_path}%"))
    if no_transfer == "yes":
        conds.append(f["nt_clause"])
    elif no_transfer == "no":
        conds.append(not_(f["nt_clause"]))
    if processed == "yes":
        conds.append(f["proc_clause"])
    elif processed == "no":
        conds.append(not_(f["proc_clause"]))

    stmt = select(Node)
    if jira is not None:
        stmt = stmt.join(Annotation, Annotation.node_id == Node.id)
        conds.append(Annotation.jira_ticket == jira)

    stmt = stmt.where(and_(*conds))

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    col = _SORT_COLUMNS.get(sort, Node.full_path)
    col = col.desc() if direction == "desc" else col.asc()
    stmt = stmt.order_by(col).offset((page - 1) * page_size).limit(page_size)

    nodes = list(db.execute(stmt).scalars().all())
    items = build_node_outs(
        db, nodes, nt_clause=f["nt_clause"], proc_clause=f["proc_clause"],
    )
    return {"total": int(total), "page": page, "page_size": page_size, "items": items}


@router.post("/type-counts")
def folder_type_counts(req: FolderTypeCountRequest, db: Session = Depends(get_db)):
    """How many files (optionally of given types) are under each folder."""
    result = []
    for nid in req.node_ids:
        node = db.get(Node, nid)
        if not node:
            result.append({"node_id": nid, "error": "not found"})
            continue
        stats = services.folder_stats(
            db, node, types=req.types,
            accessed_after=req.accessed_after, accessed_before=req.accessed_before,
        )
        result.append({
            "node_id": nid,
            "name": node.name,
            "full_path": node.full_path,
            "file_count": stats["file_count"],
            "total_size": stats["total_size"],
        })
    return {"results": result}


def _single_node_out(db: Session, node: Node) -> NodeOut:
    f = services.build_filters(db, node.dataset_id)
    return build_node_outs(
        db, [node], nt_clause=f["nt_clause"], proc_clause=f["proc_clause"]
    )[0]


@router.get("/{node_id}", response_model=NodeOut)
def get_node(node_id: int, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return _single_node_out(db, node)


@router.get("/{node_id}/counts", response_model=CountsOut)
def counts(node_id: int, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return services.folder_counts(db, node)


@router.get("/{node_id}/stats", response_model=FolderStatsOut)
def stats(
    node_id: int,
    types: list[str] | None = Query(None),
    accessed_after: date | None = Query(None),
    accessed_before: date | None = Query(None),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return services.folder_stats(
        db, node, types=types, accessed_after=accessed_after,
        accessed_before=accessed_before,
    )


@router.get("/{node_id}/type-breakdown", response_model=list[TypeBreakdownRow])
def type_breakdown(
    node_id: int,
    types: list[str] | None = Query(None),
    accessed_after: date | None = Query(None),
    accessed_before: date | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return services.type_breakdown(
        db, node, types=types, accessed_after=accessed_after,
        accessed_before=accessed_before, search=search,
    )


@router.patch("/{node_id}/annotation", response_model=NodeOut)
def update_annotation(
    node_id: int, payload: AnnotationUpdate, db: Session = Depends(get_db)
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    # Only apply fields the client actually sent (so we can clear vs ignore).
    values = payload.model_dump(exclude_unset=True)
    services.upsert_annotation(db, node, values)
    db.commit()
    db.refresh(node)
    return _single_node_out(db, node)


@router.post("/{node_id}/folder-flag", response_model=NodeOut)
def folder_flag(node_id: int, payload: FolderFlagUpdate, db: Session = Depends(get_db)):
    """Set/clear a rollup boolean (no_transfer/processed) on a folder.

    No filter -> the whole subtree: clear any descendant overrides, then set the
    folder's own value (so every file is effectively marked/unmarked).
    With a type/last-accessed filter -> only the matching files are touched and
    the folder's own value is left alone (it becomes indeterminate).
    """
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if not node.is_dir:
        raise HTTPException(status_code=400, detail="Node is not a folder")
    if payload.field not in services.BOOLEAN_FLAG_FIELDS:
        raise HTTPException(status_code=400, detail="Unsupported field")

    scoped = bool(payload.types or payload.accessed_after or payload.accessed_before)
    field = payload.field
    if scoped:
        # Only the matching files; leave the folder's own value untouched.
        if payload.value is None:
            services.clear_field_under(
                db, node, field, include_self=False, files_only=True,
                types=payload.types, accessed_after=payload.accessed_after,
                accessed_before=payload.accessed_before,
            )
        else:
            services.bulk_set_under(
                db, node, {field: payload.value}, include_self=False,
                files_only=True, types=payload.types,
                accessed_after=payload.accessed_after,
                accessed_before=payload.accessed_before,
            )
    else:
        # Whole subtree: wipe descendant overrides so the folder's value governs.
        services.clear_field_under(db, node, field, include_self=True)
        if payload.value is not None:
            services.upsert_annotation(db, node, {field: payload.value})
    db.commit()
    db.refresh(node)
    return _single_node_out(db, node)


@router.post("/bulk-annotation")
def bulk_annotation(payload: BulkAnnotationUpdate, db: Session = Depends(get_db)):
    node = db.get(Node, payload.node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    values = payload.values.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="No values provided")
    count = services.bulk_set_under(
        db, node, values,
        include_self=payload.include_self,
        files_only=payload.files_only,
        types=payload.types,
        accessed_after=payload.accessed_after,
        accessed_before=payload.accessed_before,
    )
    db.commit()
    return {"updated": count}
