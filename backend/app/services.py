"""Business logic: inheritance resolution + tree aggregate queries."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from .models import Annotation, Node

ANNOTATION_FIELDS = (
    "processed",
    "keep",
    "target_location",
    "jira_ticket",
    "comment",
    "user_name",
)


def ancestor_ids_self_first(mat_path: str) -> list[int]:
    """['/1/5/23/'] -> [23, 5, 1] (self first, then up to the root)."""
    ids = [int(x) for x in mat_path.strip("/").split("/") if x]
    return list(reversed(ids))


def resolve_effective(db: Session, nodes: list[Node]) -> dict[int, dict]:
    """Compute effective (inherited+override) annotation values for ``nodes``.

    Returns ``{node_id: {"effective": {...}, "source": {...}, "own": {...}}}``
    where *source* maps each field to the node id that supplied the value (so the
    UI can distinguish an inherited value from one set directly on the node) and
    *own* is the node's own override values (None where not set).
    """
    needed: set[int] = set()
    for n in nodes:
        needed.update(ancestor_ids_self_first(n.mat_path))

    ann_map: dict[int, Annotation] = {}
    if needed:
        rows = db.execute(
            select(Annotation).where(Annotation.node_id.in_(needed))
        ).scalars()
        for a in rows:
            ann_map[a.node_id] = a

    out: dict[int, dict] = {}
    for n in nodes:
        eff = {f: None for f in ANNOTATION_FIELDS}
        src = {f: None for f in ANNOTATION_FIELDS}
        for nid in ancestor_ids_self_first(n.mat_path):
            a = ann_map.get(nid)
            if a is None:
                continue
            for f in ANNOTATION_FIELDS:
                if eff[f] is None:
                    v = getattr(a, f)
                    if v is not None and v != "":
                        eff[f] = v
                        src[f] = nid
        own_ann = ann_map.get(n.id)
        own = (
            {f: getattr(own_ann, f) for f in ANNOTATION_FIELDS}
            if own_ann
            else {f: None for f in ANNOTATION_FIELDS}
        )
        out[n.id] = {"effective": eff, "source": src, "own": own}
    return out


def _descendant_files_filter(node: Node, *, types, accessed_after, accessed_before):
    """Build the WHERE clause for files strictly under ``node`` matching filters."""
    conds = [
        Node.dataset_id == node.dataset_id,
        Node.mat_path.like(f"{node.mat_path}%"),
        Node.id != node.id,
        Node.is_dir.is_(False),
    ]
    _apply_file_filters(conds, types=types, accessed_after=accessed_after,
                        accessed_before=accessed_before)
    return conds


def _apply_file_filters(conds: list, *, types, accessed_after, accessed_before):
    if types:
        conds.append(Node.file_type.in_(list(types)))
    if accessed_after:
        conds.append(Node.last_accessed >= accessed_after)
    if accessed_before:
        conds.append(Node.last_accessed <= accessed_before)


def folder_stats(
    db: Session,
    node: Node,
    *,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
) -> dict:
    """Recursive file count + total size for files under ``node`` (filtered)."""
    conds = _descendant_files_filter(
        node, types=types, accessed_after=accessed_after, accessed_before=accessed_before
    )
    row = db.execute(
        select(func.count(Node.id), func.coalesce(func.sum(Node.size_bytes), 0)).where(
            and_(*conds)
        )
    ).one()
    return {"file_count": int(row[0]), "total_size": int(row[1])}


def folder_counts(db: Session, node: Node) -> dict:
    """Total nested file + folder counts (unfiltered) under ``node``."""
    base = [
        Node.dataset_id == node.dataset_id,
        Node.mat_path.like(f"{node.mat_path}%"),
        Node.id != node.id,
    ]
    files = db.execute(
        select(func.count(Node.id)).where(and_(*base, Node.is_dir.is_(False)))
    ).scalar_one()
    folders = db.execute(
        select(func.count(Node.id)).where(and_(*base, Node.is_dir.is_(True)))
    ).scalar_one()
    return {"file_count": int(files), "folder_count": int(folders)}


def type_breakdown(
    db: Session,
    node: Node,
    *,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    search: str | None = None,
) -> list[dict]:
    """File-type histogram (count + total size) for files under ``node``."""
    conds = _descendant_files_filter(
        node, types=types, accessed_after=accessed_after, accessed_before=accessed_before
    )
    if search:
        conds.append(Node.file_type.ilike(f"%{search}%"))
    stmt: Select = (
        select(
            Node.file_type,
            func.count(Node.id).label("count"),
            func.coalesce(func.sum(Node.size_bytes), 0).label("total_size"),
        )
        .where(and_(*conds))
        .group_by(Node.file_type)
        .order_by(func.count(Node.id).desc())
    )
    return [
        {"file_type": r[0], "count": int(r[1]), "total_size": int(r[2])}
        for r in db.execute(stmt).all()
    ]


def get_node(db: Session, node_id: int) -> Node | None:
    return db.get(Node, node_id)


def upsert_annotation(db: Session, node: Node, values: dict) -> Annotation:
    """Set override values on a node (None clears -> inherit again).

    Only keys present in ``values`` are touched, so a partial update leaves other
    fields alone.
    """
    ann = db.get(Annotation, node.id)
    if ann is None:
        ann = Annotation(node_id=node.id, dataset_id=node.dataset_id)
        db.add(ann)
    for k, v in values.items():
        if k in ANNOTATION_FIELDS:
            setattr(ann, k, v)
    db.flush()
    return ann


def bulk_set_under(
    db: Session,
    folder: Node,
    values: dict,
    *,
    include_self: bool = True,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    files_only: bool = False,
) -> int:
    """Write override values onto every node under ``folder`` matching filters.

    This is the explicit "stamp every descendant" path (used by bulk edit when a
    user wants concrete values on each row, e.g. assigning a JIRA ticket to a
    filtered set). Normal folder marking relies on inheritance and does not call
    this.
    Returns the number of nodes whose annotation was written.
    """
    conds = [
        Node.dataset_id == folder.dataset_id,
        Node.mat_path.like(f"{folder.mat_path}%"),
    ]
    if not include_self:
        conds.append(Node.id != folder.id)
    if files_only:
        conds.append(Node.is_dir.is_(False))
    _apply_file_filters(conds, types=types, accessed_after=accessed_after,
                        accessed_before=accessed_before)

    node_ids = [
        r[0] for r in db.execute(select(Node.id).where(and_(*conds))).all()
    ]
    if not node_ids:
        return 0

    existing = {
        a.node_id: a
        for a in db.execute(
            select(Annotation).where(Annotation.node_id.in_(node_ids))
        ).scalars()
    }
    clean = {k: v for k, v in values.items() if k in ANNOTATION_FIELDS}
    for nid in node_ids:
        ann = existing.get(nid)
        if ann is None:
            ann = Annotation(node_id=nid, dataset_id=folder.dataset_id)
            db.add(ann)
        for k, v in clean.items():
            setattr(ann, k, v)
    db.flush()
    return len(node_ids)
