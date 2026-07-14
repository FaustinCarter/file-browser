"""Node detail, aggregates, search grid, and annotation editing."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..models import Node
from ..schemas import (
    AnnotationUpdate,
    BulkAnnotationUpdate,
    CountsOut,
    FolderFlagUpdate,
    FolderStatsOut,
    FolderTypeCountRequest,
    GridBulkUpdate,
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
    jira: str | None = Query(None, description="effective JIRA value, or '__none__' for unassigned"),
    assignee: str | None = Query(None, description="effective assignee, or '__none__' for unassigned"),
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

    The processed / no_transfer / jira / assignee filters match a node's
    **effective** value (own or inherited from a parent folder). ``owner`` filters
    the read-only file-system owner from the CSV.
    """
    try:
        conds, _f = services.grid_filter_conds(
            db, dataset_id, q=q, types=types, owner=owner, is_dir=is_dir,
            jira=jira, assignee=assignee, processed=processed,
            no_transfer=no_transfer, accessed_after=accessed_after,
            accessed_before=accessed_before, under_node_id=under_node_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Two separate queries -- an exact COUNT(*) and a top-N-sorted page fetch --
    # rather than a combined `count(*) OVER ()`: a window function must
    # materialize and count the *entire* filtered+ordered result before a
    # LIMIT can apply, which defeats Postgres's top-N heapsort (cheap partial
    # sort that stops once it has `page_size` rows) and, at millions of
    # matching rows, spills to disk. Two plain queries let the planner pick the
    # cheap plan for each independently (index-only count; top-N sort fetch).
    # The count only needs `id` -- selecting whole rows here just to discard
    # them would carry every column through the count needlessly.
    total = db.execute(
        select(func.count()).select_from(select(Node.id).where(and_(*conds)).subquery())
    ).scalar_one()

    col = _SORT_COLUMNS.get(sort, Node.full_path)
    col = col.desc() if direction == "desc" else col.asc()
    stmt = (
        select(Node)
        .where(and_(*conds))
        .order_by(col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    nodes = list(db.execute(stmt).scalars().all())

    # Flat grid: folder rows show total (unfiltered) rollups, so no view_filter.
    items = build_node_outs(db, nodes)
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
    return build_node_outs(db, [node])[0]


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
    no_transfer: str | None = Query(None),
    processed: str | None = Query(None),
    jira: str | None = Query(None),
    assignee: str | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    f = services.build_filters(
        db, node.dataset_id, types=types, accessed_after=accessed_after,
        accessed_before=accessed_before, no_transfer=no_transfer,
        processed=processed, jira=jira, assignee=assignee,
    )
    return services.type_breakdown(db, node, view_filter=f["view_filter"], search=search)


@router.patch("/{node_id}/annotation", response_model=NodeOut)
def update_annotation(
    node_id: int,
    payload: AnnotationUpdate,
    actor: str | None = Header(default=None, alias="X-Actor"),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    # Only apply fields the client actually sent (so we can clear vs ignore).
    values = payload.model_dump(exclude_unset=True)
    services.upsert_annotation(db, node, values, actor=actor)
    if services.materialized_fields_touched(values):
        services.refresh_effective_for_node(db, node.dataset_id, node.id, is_dir=node.is_dir, values=values)
    db.commit()
    db.refresh(node)
    return _single_node_out(db, node)


@router.post("/{node_id}/folder-flag", response_model=NodeOut)
def folder_flag(
    node_id: int,
    payload: FolderFlagUpdate,
    actor: str | None = Header(default=None, alias="X-Actor"),
    db: Session = Depends(get_db),
):
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
                accessed_before=payload.accessed_before, actor=actor,
            )
        else:
            services.bulk_set_under(
                db, node, {field: payload.value}, include_self=False,
                files_only=True, types=payload.types,
                accessed_after=payload.accessed_after,
                accessed_before=payload.accessed_before, actor=actor,
            )
        # Descendant files' own annotations changed, but the folder's own
        # value is untouched by construction -- no own_values, or this would
        # wrongly stomp the folder's (still-indeterminate) effective value.
        services.refresh_effective_for_subtree(db, node.dataset_id, node.id, fields=(field,))
    else:
        # Whole subtree: wipe descendant overrides so the folder's value governs.
        services.clear_field_under(db, node, field, include_self=True, actor=actor)
        if payload.value is not None:
            services.upsert_annotation(db, node, {field: payload.value}, actor=actor)
        # `field` is always one of BOOLEAN_FLAG_FIELDS, both materialized. The
        # folder's own value was just written (or cleared) above.
        services.refresh_effective_for_subtree(
            db, node.dataset_id, node.id, fields=(field,), own_values={field: payload.value},
        )
    db.commit()
    db.refresh(node)
    return _single_node_out(db, node)


@router.post("/bulk-annotation")
def bulk_annotation(
    payload: BulkAnnotationUpdate,
    actor: str | None = Header(default=None, alias="X-Actor"),
    db: Session = Depends(get_db),
):
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
        actor=actor,
    )
    fields = services.materialized_fields_touched(values)
    if fields:
        # bulk_set_under's own-node match is `mat_path LIKE folder.mat_path%`,
        # which folder always satisfies trivially -- so the folder itself is
        # among the stamped nodes (and its own annotation IS part of this
        # write) exactly when include_self is set and files_only isn't
        # forcing folders out of the match.
        own_touched = payload.include_self and not payload.files_only
        services.refresh_effective_for_subtree(
            db, node.dataset_id, node.id, fields=fields,
            own_values=values if own_touched else None,
        )
    db.commit()
    return {"updated": count}


@router.post("/bulk-by-filter")
def bulk_by_filter(
    payload: GridBulkUpdate,
    actor: str | None = Header(default=None, alias="X-Actor"),
    db: Session = Depends(get_db),
):
    """Apply an edit to every row matching the grid filter (all pages at once)."""
    values = payload.values.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="No values provided")
    try:
        count, touched_ids = services.bulk_set_matching(
            db,
            dataset_id=payload.dataset_id,
            values=values,
            actor=actor,
            q=payload.q,
            types=payload.types,
            owner=payload.owner,
            # files_only forces the match to files so we don't create a per-folder
            # override for every matched folder.
            is_dir=False if payload.files_only else payload.is_dir,
            jira=payload.jira,
            assignee=payload.assignee,
            processed=payload.processed,
            no_transfer=payload.no_transfer,
            accessed_after=payload.accessed_after,
            accessed_before=payload.accessed_before,
            under_node_id=payload.under_node_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # The grid filter can match a scattered set spanning the whole dataset (no
    # single bounding folder), so scope the refresh to exactly what matched.
    # Every one of touched_ids got `values` written to its own annotation
    # (that's what bulk_set_matching just did), so the fast literal-value
    # path applies uniformly here -- no own/descendant distinction needed.
    if services.materialized_fields_touched(values):
        services.refresh_effective_for_nodes(db, payload.dataset_id, touched_ids, values=values)
    db.commit()
    return {"updated": count}
