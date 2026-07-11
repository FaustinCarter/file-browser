"""Business logic: inheritance resolution + tree aggregate queries."""
from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Select,
    and_,
    func,
    literal,
    not_,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Session

from .models import Annotation, Node

# Editable, inheritable annotation fields.
ANNOTATION_FIELDS = (
    "processed",
    "no_transfer",
    "target_location",
    "jira_ticket",
    "comment",
    "assignee",
)

# Booleans that use the folder rollup / hide-filter behaviour.
BOOLEAN_FLAG_FIELDS = ("processed", "no_transfer")

# Sentinel meaning "filter to records with no effective value for this field".
UNASSIGNED = "__none__"


# Effective-value filtering.
#
# A node's effective value for a field is its own value, else the value inherited
# from the nearest ancestor. Ancestors are always folders (files are leaves), so
# a *file* override only ever affects itself. That lets us keep the generated SQL
# small no matter how many rows are annotated:
#   * "own value" is checked with a single subquery (IN / NOT IN annotations),
#     so applying a flag to 145k files doesn't produce 145k clauses; and
#   * inheritance is expressed as materialized-path regions over *folder*
#     overrides only (of which there are few).


def _folder_overrides(db: Session, dataset_id: int, field: str):
    """``[(mat_path, value), ...]`` for the folders that set ``field`` (few)."""
    col = getattr(Annotation, field)
    return db.execute(
        select(Node.mat_path, col)
        .join(Annotation, Annotation.node_id == Node.id)
        .where(
            Annotation.dataset_id == dataset_id,
            col.isnot(None),
            Node.is_dir.is_(True),
        )
    ).all()


def _own_ids(dataset_id: int, field: str, *, value=..., notnull: bool = False):
    col = getattr(Annotation, field)
    q = select(Annotation.node_id).where(Annotation.dataset_id == dataset_id)
    if notnull:
        return q.where(col.isnot(None))
    return q.where(col == value)


def _direct_children(paths: list[str]) -> dict[str, list[str]]:
    """Map each override path to its nearest descendant override paths."""
    paths = sorted(paths)
    direct: dict[str, list[str]] = {p: [] for p in paths}
    stack: list[str] = []
    for p in paths:
        while stack and not p.startswith(stack[-1]):
            stack.pop()
        if stack:
            direct[stack[-1]].append(p)
        stack.append(p)
    return direct


def _folder_region_clause(folders: list, value):
    """OR of the subtree regions governed by a folder override with ``value``.

    Each matching folder contributes its subtree minus the subtrees of its
    nearest descendant folder overrides (governed by a deeper value). Returns
    None if no folder sets ``value``.
    """
    if not folders:
        return None
    direct = _direct_children([mp for mp, _ in folders])
    clauses = []
    for mp, v in folders:
        if v != value:
            continue
        region = Node.mat_path.like(mp + "%")
        kids = direct.get(mp, [])
        if kids:
            region = and_(region, not_(or_(*[Node.mat_path.like(k + "%") for k in kids])))
        clauses.append(region)
    return or_(*clauses) if clauses else None


def effective_equals_clause(db: Session, dataset_id: int, field: str, value):
    """SQL expression: true for nodes whose *effective* ``field`` equals ``value``."""
    own_match = Node.id.in_(_own_ids(dataset_id, field, value=value))
    region = _folder_region_clause(_folder_overrides(db, dataset_id, field), value)
    if region is None:
        return own_match
    # Inherited only where the node has no own value of its own.
    own_is_null = Node.id.notin_(_own_ids(dataset_id, field, notnull=True))
    return or_(own_match, and_(own_is_null, region))


def effective_isnull_clause(db: Session, dataset_id: int, field: str):
    """SQL expression: true for nodes with no effective value for ``field``."""
    own_is_null = Node.id.notin_(_own_ids(dataset_id, field, notnull=True))
    folders = _folder_overrides(db, dataset_id, field)
    if not folders:
        return own_is_null
    not_under_folder = not_(or_(*[Node.mat_path.like(mp + "%") for mp, _ in folders]))
    return and_(own_is_null, not_under_folder)


def effective_true_clause(db: Session, dataset_id: int, field: str):
    """Effective value is boolean True (used by the rollups / hide filters)."""
    return effective_equals_clause(db, dataset_id, field, True)


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
        out[n.id] = {
            "effective": eff,
            "source": src,
            "own": own,
            # Audit: the node's own last-touched info (None if never touched).
            "updated_at": own_ann.updated_at if own_ann else None,
            "updated_by": own_ann.updated_by if own_ann else None,
        }
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


def build_filters(
    db: Session,
    dataset_id: int,
    *,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    no_transfer: str | None = None,
    processed: str | None = None,
    jira: str | None = None,
    assignee: str | None = None,
) -> dict:
    """Translate request filter params into reusable SQL clauses.

    ``no_transfer`` / ``processed`` are tri-state: None (any), "yes" (show only
    effectively-marked), or "no" (hide effectively-marked). ``jira`` / ``assignee``
    filter on the *effective* value: a specific string matches that value, the
    ``UNASSIGNED`` sentinel ("__none__") matches records with no effective value.
    The returned ``view_filter`` combines every active filter; ``nt_clause`` /
    ``proc_clause`` are the raw effective-true expressions (used for folder
    rollups regardless of whether a filter is active).
    """
    nt_clause = effective_true_clause(db, dataset_id, "no_transfer")
    proc_clause = effective_true_clause(db, dataset_id, "processed")
    conds = []
    if types:
        conds.append(Node.file_type.in_(list(types)))
    if accessed_after:
        conds.append(Node.last_accessed >= accessed_after)
    if accessed_before:
        conds.append(Node.last_accessed <= accessed_before)
    if no_transfer == "yes":
        conds.append(nt_clause)
    elif no_transfer == "no":
        conds.append(not_(nt_clause))
    if processed == "yes":
        conds.append(proc_clause)
    elif processed == "no":
        conds.append(not_(proc_clause))

    def value_filter(field: str, value: str | None):
        if not value:
            return
        if value == UNASSIGNED:
            conds.append(effective_isnull_clause(db, dataset_id, field))
        else:
            conds.append(effective_equals_clause(db, dataset_id, field, value))

    value_filter("jira_ticket", jira)
    value_filter("assignee", assignee)

    return {
        "view_filter": and_(*conds) if conds else None,
        "nt_clause": nt_clause,
        "proc_clause": proc_clause,
        "filter_active": bool(conds),
    }


def distinct_values(db: Session, dataset_id: int, field: str) -> list[str]:
    """Distinct non-null values assigned for ``field`` (for filter dropdowns)."""
    if field not in ANNOTATION_FIELDS:
        return []
    col = getattr(Annotation, field)
    rows = db.execute(
        select(col)
        .where(Annotation.dataset_id == dataset_id, col.isnot(None))
        .distinct()
        .order_by(col)
    ).all()
    return [r[0] for r in rows]


def folder_metrics(
    db: Session,
    node: Node,
    *,
    view_filter=None,
    nt_clause=None,
    proc_clause=None,
) -> dict:
    """One-query rollup over a folder's descendant files.

    Returns the filtered file count/size (what the tree should display given the
    active filters) plus the total file count and how many files are *effectively*
    marked no_transfer / processed (for the folder's tri-state checkbox).
    """
    base = [
        Node.dataset_id == node.dataset_id,
        Node.mat_path.like(f"{node.mat_path}%"),
        Node.id != node.id,
        Node.is_dir.is_(False),
    ]
    filtered_count = func.count().filter(view_filter) if view_filter is not None else func.count()
    filtered_size = (
        func.coalesce(func.sum(Node.size_bytes).filter(view_filter), 0)
        if view_filter is not None
        else func.coalesce(func.sum(Node.size_bytes), 0)
    )
    nt = func.count().filter(nt_clause) if nt_clause is not None else literal(0)
    pc = func.count().filter(proc_clause) if proc_clause is not None else literal(0)

    row = db.execute(
        select(func.count(), filtered_count, filtered_size, nt, pc).where(and_(*base))
    ).one()
    return {
        "total_files": int(row[0]),
        "filtered_file_count": int(row[1]),
        "filtered_total_size": int(row[2]),
        "no_transfer_marked": int(row[3]),
        "processed_marked": int(row[4]),
    }


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
    view_filter=None,
    search: str | None = None,
) -> list[dict]:
    """File-type histogram (count + total size) for files under ``node``.

    ``view_filter`` is the combined filter clause (type / last-accessed / flags /
    assignee / jira) so the breakdown matches the folder's filtered file count.
    """
    conds = [
        Node.dataset_id == node.dataset_id,
        Node.mat_path.like(f"{node.mat_path}%"),
        Node.id != node.id,
        Node.is_dir.is_(False),
    ]
    if view_filter is not None:
        conds.append(view_filter)
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


def _stamp(ann: Annotation, actor: str | None) -> None:
    """Record who touched this annotation and when (server time)."""
    ann.updated_by = actor
    ann.updated_at = func.now()


def upsert_annotation(
    db: Session, node: Node, values: dict, *, actor: str | None = None
) -> Annotation:
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
    _stamp(ann, actor)
    db.flush()
    return ann


def clear_field_under(
    db: Session,
    folder: Node,
    field: str,
    *,
    include_self: bool = True,
    files_only: bool = False,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    actor: str | None = None,
) -> int:
    """Null out ``field`` on existing annotations within ``folder``'s subtree.

    Updates only rows that already exist (no annotation rows are created just to
    store a NULL), so it's cheap even on huge subtrees.
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
    subq = select(Node.id).where(and_(*conds))
    result = db.execute(
        update(Annotation)
        .where(Annotation.node_id.in_(subq))
        .values(**{field: None, "updated_by": actor, "updated_at": func.now()})
    )
    db.flush()
    return result.rowcount or 0


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
    actor: str | None = None,
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
    return _apply_annotation_values(
        db, folder.dataset_id, node_ids, values, actor
    )


def _apply_annotation_values(
    db: Session, dataset_id: int, node_ids: list[int], values: dict,
    actor: str | None, *, chunk: int = 5000,
) -> int:
    """Upsert ``values`` (+ audit) onto each node in ``node_ids``, in chunks."""
    clean = {k: v for k, v in values.items() if k in ANNOTATION_FIELDS}
    if not node_ids or not clean:
        return 0
    total = 0
    for i in range(0, len(node_ids), chunk):
        part = node_ids[i:i + chunk]
        existing = {
            a.node_id: a
            for a in db.execute(
                select(Annotation).where(Annotation.node_id.in_(part))
            ).scalars()
        }
        for nid in part:
            ann = existing.get(nid)
            if ann is None:
                ann = Annotation(node_id=nid, dataset_id=dataset_id)
                db.add(ann)
            for k, v in clean.items():
                setattr(ann, k, v)
            _stamp(ann, actor)
        db.flush()
        total += len(part)
    return total


def grid_filter_conds(
    db: Session,
    dataset_id: int,
    *,
    q: str | None = None,
    types: list[str] | None = None,
    owner: str | None = None,
    is_dir: bool | None = None,
    jira: str | None = None,
    assignee: str | None = None,
    processed: str | None = None,
    no_transfer: str | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    under_node_id: int | None = None,
) -> tuple[list, dict]:
    """Build the WHERE conditions for the grid/search (shared by search + bulk).

    Returns ``(conds, filters)`` where ``filters`` is the build_filters() result
    (so callers can reuse its rollup clauses).
    """
    conds = [Node.dataset_id == dataset_id]
    if q:
        conds.append(Node.full_path.ilike(f"%{q}%"))
    if types:
        conds.append(Node.file_type.in_(list(types)))
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
        if parent is None:
            raise ValueError("under_node_id not found")
        conds.append(Node.mat_path.like(f"{parent.mat_path}%"))
    filters = build_filters(
        db, dataset_id, no_transfer=no_transfer, processed=processed,
        jira=jira, assignee=assignee,
    )
    if filters["view_filter"] is not None:
        conds.append(filters["view_filter"])
    return conds, filters


def bulk_set_matching(
    db: Session, *, dataset_id: int, values: dict, actor: str | None = None, **filters
) -> int:
    """Apply ``values`` to every node matching the grid filter (all pages)."""
    conds, _ = grid_filter_conds(db, dataset_id, **filters)
    node_ids = [r[0] for r in db.execute(select(Node.id).where(and_(*conds))).all()]
    return _apply_annotation_values(db, dataset_id, node_ids, values, actor)
