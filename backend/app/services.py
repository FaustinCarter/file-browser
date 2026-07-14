"""Business logic: inheritance resolution + tree aggregate queries."""
from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Select,
    and_,
    func,
    select,
    update,
)
from sqlalchemy.orm import Session, aliased

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

# Text (string) annotation fields, where empty string == "no value".
TEXT_FIELDS = ("target_location", "jira_ticket", "comment", "assignee")

# The subset of ANNOTATION_FIELDS that get a materialized `<field>_eff` column
# on Node (i.e. the ones ViewFilter actually filters on). target_location and
# comment are shown/edited but never filtered, so they stay CTE/ancestor-walk
# only (resolve_effective) and don't need a materialized column.
MATERIALIZED_FIELDS = ("no_transfer", "processed", "jira_ticket", "assignee")

# Sentinel meaning "filter to records with no effective value for this field".
UNASSIGNED = "__none__"


# Effective-value resolution via a recursive CTE.
#
# A node's effective value for a field is its own value, else the value inherited
# from the nearest ancestor folder (files are leaves, so a file override only ever
# affects itself). We resolve this with a single top-down tree walk by parent_id:
# each child COALESCEs its own value over its parent's already-resolved value. The
# walk visits every node once (O(nodes)), independent of how many overrides exist.
#
# This CTE is O(nodes) per evaluation, which is fine as an occasional per-request
# cost but becomes the dominant cost at millions of rows if run on every read (it
# was, until the refactor below). It is now used in exactly two places:
#   1. `refresh_effective_columns` -- write time, once per mutating request, to
#      materialize `Node.<field>_eff` for the fields that are actually filtered on
#      (`MATERIALIZED_FIELDS`). Reads then filter/rollup on those plain columns.
#   2. Kept directly usable (`effective_true_clause` etc.) for tests/tools that
#      want the CTE-derived predicate itself, e.g. the "bounded SQL" regression
#      test -- it is still the correctness reference, just off the read hot path.


def _own_expr(ann, field):
    """Own value of ``field``, with empty-string text normalised to NULL.

    Matching ``resolve_effective``'s ``v != ""`` rule keeps SQL inheritance and the
    Python reference in lock-step (see the equivalence test)."""
    col = getattr(ann, field)
    return func.nullif(col, "") if field in TEXT_FIELDS else col


def effective_cte(dataset_id: int):
    """Recursive CTE with one row per node: ``id`` + a ``<field>_eff`` column per
    inheritable field, holding that node's effective value."""
    a0 = aliased(Annotation)
    anchor = (
        select(
            Node.id.label("id"),
            *[_own_expr(a0, f).label(f"{f}_eff") for f in ANNOTATION_FIELDS],
        )
        .select_from(Node)
        .join(a0, a0.node_id == Node.id, isouter=True)
        .where(Node.dataset_id == dataset_id, Node.parent_id.is_(None))
    )
    eff = anchor.cte("eff", recursive=True)
    child = aliased(Node)
    ac = aliased(Annotation)
    rec = (
        select(
            child.id.label("id"),
            *[
                func.coalesce(_own_expr(ac, f), eff.c[f"{f}_eff"]).label(f"{f}_eff")
                for f in ANNOTATION_FIELDS
            ],
        )
        .select_from(eff)
        .join(child, and_(child.parent_id == eff.c.id, child.dataset_id == dataset_id))
        .join(ac, ac.node_id == child.id, isouter=True)
    )
    return eff.union_all(rec)


def eff_true_pred(node, cte, field):
    """Predicate: ``node``'s effective ``field`` is boolean True."""
    return node.id.in_(select(cte.c.id).where(cte.c[f"{field}_eff"].is_(True)))


def eff_equals_pred(node, cte, field, value):
    """Predicate: ``node``'s effective ``field`` equals ``value``."""
    return node.id.in_(select(cte.c.id).where(cte.c[f"{field}_eff"] == value))


def eff_isnull_pred(node, cte, field):
    """Predicate: ``node`` has no effective value for ``field``."""
    return node.id.in_(select(cte.c.id).where(cte.c[f"{field}_eff"].is_(None)))


# Thin wrappers that build their own CTE and bind to ``Node`` (for callers/tests
# that just want a standalone WHERE predicate).
def effective_equals_clause(db: Session, dataset_id: int, field: str, value):
    return eff_equals_pred(Node, effective_cte(dataset_id), field, value)


def effective_isnull_clause(db: Session, dataset_id: int, field: str):
    return eff_isnull_pred(Node, effective_cte(dataset_id), field)


def effective_true_clause(db: Session, dataset_id: int, field: str):
    return eff_true_pred(Node, effective_cte(dataset_id), field)


def refresh_effective_columns(db: Session, dataset_id: int) -> None:
    """Recompute ``Node.<field>_eff`` for every node in ``dataset_id``.

    Runs the recursive CTE once (O(nodes)) and writes its result back onto the
    materialized columns via a single ``UPDATE ... FROM`` statement.

    This is O(dataset size), which is fine for a one-time migration backfill
    (see ``main._init_schema``) but was measured to take well over a minute on
    a 2.2M-row dataset -- unusable as a per-edit cost. Interactive writes use
    the *scoped* refresh functions below instead, which cost O(affected
    subtree) by construction. Nothing on the request path calls this function
    anymore; keep it only for the migration backfill and as the "obviously
    correct" reference the scoped functions are checked against.
    """
    eff = effective_cte(dataset_id)
    stmt = (
        update(Node)
        .values(**{f"{f}_eff": eff.c[f"{f}_eff"] for f in MATERIALIZED_FIELDS})
        .where(Node.id == eff.c.id, Node.dataset_id == dataset_id)
    )
    db.execute(stmt)


# ---- Scoped (write-time) refresh: O(affected subtree), not O(dataset) ----
#
# A single annotation edit only ever changes the effective value of (a) the
# edited node itself and (b), if it's a folder, its descendants that don't
# have a nearer override. Recomputing the *whole dataset* on every edit (as
# `refresh_effective_columns` does) is correct but pays for the entire tree
# every time, which is what made a single edit take 100+ seconds at 2.2M rows.
# These functions instead:
#   1. Recompute exactly the touched node(s)' own effective value as
#      COALESCE(its own current annotation, its parent's *current* eff
#      columns) -- O(1) per node, since the parent's columns are already
#      correct (the surrounding invariant this whole scheme relies on).
#   2. For any touched node that's a folder, walk *only* its subtree with the
#      same recursive step as `effective_cte`, seeded at the folder's just-
#      updated eff columns -- O(that folder's subtree), not O(dataset).


def _own_from_parent_select(dataset_id: int, node_ids: list[int]):
    """Each of ``node_ids``' new effective value: its own (current) annotation,
    else its parent's *current* ``<field>_eff`` (assumed already correct)."""
    ann = aliased(Annotation)
    parent = aliased(Node)
    return (
        select(
            Node.id.label("id"),
            *[
                func.coalesce(_own_expr(ann, f), getattr(parent, f"{f}_eff")).label(f"{f}_eff")
                for f in MATERIALIZED_FIELDS
            ],
        )
        .select_from(Node)
        .join(ann, ann.node_id == Node.id, isouter=True)
        .join(parent, parent.id == Node.parent_id, isouter=True)
        .where(Node.id.in_(node_ids), Node.dataset_id == dataset_id)
    )


def _refresh_own_from_parent(db: Session, dataset_id: int, node_ids: list[int]) -> None:
    if not node_ids:
        return
    sub = _own_from_parent_select(dataset_id, node_ids).subquery()
    stmt = (
        update(Node)
        .values(**{f"{f}_eff": sub.c[f"{f}_eff"] for f in MATERIALIZED_FIELDS})
        .where(Node.id == sub.c.id)
    )
    db.execute(stmt)


def _subtree_propagate_cte(dataset_id: int, root_ids: list[int]):
    """Recursive CTE seeded at ``root_ids`` (whose eff columns must already be
    current), walking down through their descendants only."""
    anchor = select(
        Node.id.label("id"),
        *[getattr(Node, f"{f}_eff").label(f"{f}_eff") for f in MATERIALIZED_FIELDS],
    ).where(Node.id.in_(root_ids))
    eff = anchor.cte("eff_subtree", recursive=True)
    child = aliased(Node)
    ac = aliased(Annotation)
    rec = (
        select(
            child.id.label("id"),
            *[
                func.coalesce(_own_expr(ac, f), eff.c[f"{f}_eff"]).label(f"{f}_eff")
                for f in MATERIALIZED_FIELDS
            ],
        )
        .select_from(eff)
        .join(child, and_(child.parent_id == eff.c.id, child.dataset_id == dataset_id))
        .join(ac, ac.node_id == child.id, isouter=True)
    )
    return eff.union_all(rec)


def _propagate_subtrees(db: Session, dataset_id: int, root_ids: list[int]) -> None:
    if not root_ids:
        return
    sub = _subtree_propagate_cte(dataset_id, root_ids)
    stmt = (
        update(Node)
        .values(**{f"{f}_eff": sub.c[f"{f}_eff"] for f in MATERIALIZED_FIELDS})
        .where(Node.id == sub.c.id)
    )
    db.execute(stmt)


def _dedupe_nested_folders(db: Session, dataset_id: int, folder_ids: list[int]) -> list[int]:
    """Drop any folder in ``folder_ids`` that is itself a descendant of another
    folder in the same list -- its ancestor's subtree walk already covers it,
    so re-walking it as its own root would just be wasted (harmless) work.

    ``mat_path`` sorts so that a folder's descendants immediately follow it
    (materialized path = literal ancestor-id prefix), so one linear pass over
    paths sorted lexicographically is enough to find the "outermost" roots.
    """
    if not folder_ids:
        return []
    rows = db.execute(
        select(Node.id, Node.mat_path)
        .where(Node.id.in_(folder_ids), Node.dataset_id == dataset_id)
        .order_by(Node.mat_path)
    ).all()
    roots: list[int] = []
    last_prefix: str | None = None
    for nid, mat_path in rows:
        if last_prefix is not None and mat_path.startswith(last_prefix):
            continue
        roots.append(nid)
        last_prefix = mat_path
    return roots


def refresh_effective_for_node(db: Session, dataset_id: int, node_id: int, *, is_dir: bool) -> None:
    """A single node's own annotation was just written (upsert/clear).

    O(1) for a file (leaf, nothing to propagate to); O(that folder's subtree)
    for a folder, since descendants may now inherit a different value.
    """
    _refresh_own_from_parent(db, dataset_id, [node_id])
    if is_dir:
        _propagate_subtrees(db, dataset_id, [node_id])


def refresh_effective_for_subtree(db: Session, dataset_id: int, folder_id: int) -> None:
    """A bulk write touched some subset of ``folder_id``'s subtree (bulk-stamp,
    scoped clear, folder-flag) -- possibly including ``folder_id`` itself.

    O(that folder's subtree), never O(dataset), regardless of how much of the
    subtree the write actually touched -- recomputing everything under one
    bounded folder is simpler and still cheap relative to the dataset.
    """
    _refresh_own_from_parent(db, dataset_id, [folder_id])
    _propagate_subtrees(db, dataset_id, [folder_id])


def refresh_effective_for_nodes(db: Session, dataset_id: int, node_ids: list[int]) -> None:
    """An arbitrary, possibly-scattered set of nodes were each just stamped
    with their own value (grid "apply to every filtered row").

    O(sum of touched folders' subtree sizes), not O(dataset): every touched
    node's own value is recomputed directly, and any touched *folders* also
    propagate to their descendants (deduped so nested touched folders aren't
    walked twice).
    """
    if not node_ids:
        return
    _refresh_own_from_parent(db, dataset_id, node_ids)
    dir_ids = [
        r[0] for r in db.execute(
            select(Node.id).where(Node.id.in_(node_ids), Node.is_dir.is_(True))
        ).all()
    ]
    roots = _dedupe_nested_folders(db, dataset_id, dir_ids)
    _propagate_subtrees(db, dataset_id, roots)


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


def _descendant_of_pred(ancestor_mat_path, descendant_mat_path):
    """"Descendant is under ancestor" as an indexable range, for a *correlated*
    (per-row) ancestor path -- e.g. a self-join over many folders at once.

    ``descendant_mat_path.like(ancestor_mat_path + '%')`` reads naturally but
    can't use the ``text_pattern_ops`` prefix index here: Postgres only
    rewrites ``LIKE 'prefix%'`` into an index range scan when the pattern is a
    constant known at plan time, and a per-row column value isn't one -- it
    silently falls back to evaluating the LIKE as a join filter over a full
    scan of every row (O(nodes) per folder, i.e. O(nodes * folders) total).
    Measured on a 2.2M-row / 555k-folder dataset: a 125-folder rollup went
    from ~39s (Seq Scan + nested-loop filter) to ~0.2s (Nested Loop + Index
    Scan) after this rewrite -- same result set, same index, different
    operators.
    ``text_pattern_ops`` indexes the C-locale pattern-comparison operators
    (``~>=~``/``~<~``, not the locale-aware default ``>=``/``<``), so an
    explicit range in those operators over ``[ancestor, ancestor-with-last-
    char-bumped)`` *is* plannable as a per-row indexed range scan. Every
    ``mat_path`` ends in ``/`` (see models.py), so bumping the trailing ``/``
    (0x2F) to ``0`` (0x30) is a safe exclusive upper bound: any descendant
    path is ``ancestor + suffix``, whose character at that position is still
    ``/`` and therefore always sorts before the bumped bound, regardless of
    ``suffix``.
    """
    upper = func.left(
        ancestor_mat_path, func.length(ancestor_mat_path) - 1
    ).concat("0").self_group()
    return and_(
        descendant_mat_path.op("~>=~")(ancestor_mat_path),
        descendant_mat_path.op("~<~")(upper),
    )


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


class ViewFilter:
    """Rebindable view filter: renders the same predicate against any node alias.

    The effective-value parts (flags / jira / assignee) are plain equality/NULL
    checks against the materialized ``<field>_eff`` columns (indexed, O(1) per
    row) -- no recursive walk at read time. The raw parts (type / last-accessed)
    reference the given node alias directly. ``build(node)`` is called with
    ``Node`` for the flat grid/search and with the descendant alias for the
    folder rollups, so search, rollups, and the type breakdown all share one
    filter definition (no drift).
    """

    def __init__(
        self, *, types=None, accessed_after=None, accessed_before=None,
        no_transfer=None, processed=None, jira=None, assignee=None,
    ):
        self.types = types
        self.accessed_after = accessed_after
        self.accessed_before = accessed_before
        self.no_transfer = no_transfer
        self.processed = processed
        self.jira = jira
        self.assignee = assignee

    @property
    def active(self) -> bool:
        return any((
            self.types, self.accessed_after, self.accessed_before,
            self.no_transfer, self.processed, self.jira, self.assignee,
        ))

    def build(self, node):
        """Return the combined predicate bound to ``node`` (or None if inactive)."""
        conds = []
        if self.types:
            conds.append(node.file_type.in_(list(self.types)))
        if self.accessed_after:
            conds.append(node.last_accessed >= self.accessed_after)
        if self.accessed_before:
            conds.append(node.last_accessed <= self.accessed_before)
        for field, state in (("no_transfer", self.no_transfer),
                             ("processed", self.processed)):
            col = getattr(node, f"{field}_eff")
            if state == "yes":
                conds.append(col.is_(True))
            elif state == "no":
                # "not effectively marked" == effective false OR null; IS NOT
                # TRUE is NULL-safe on a plain boolean column.
                conds.append(col.isnot(True))
        for field, value in (("jira_ticket", self.jira), ("assignee", self.assignee)):
            if not value:
                continue
            col = getattr(node, f"{field}_eff")
            if value == UNASSIGNED:
                conds.append(col.is_(None))
            else:
                conds.append(col == value)
        return and_(*conds) if conds else None


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
    """Build a rebindable ``ViewFilter`` over the materialized effective columns.

    ``no_transfer`` / ``processed`` are tri-state: None (any), "yes" (show only
    effectively-marked), or "no" (hide effectively-marked). ``jira`` / ``assignee``
    filter on the *effective* value: a specific string matches that value, the
    ``UNASSIGNED`` sentinel ("__none__") matches records with no effective value.
    Returns the ``view_filter`` spec and whether any filter is active.
    """
    vf = ViewFilter(
        types=types, accessed_after=accessed_after,
        accessed_before=accessed_before, no_transfer=no_transfer,
        processed=processed, jira=jira, assignee=assignee,
    )
    return {"view_filter": vf, "filter_active": vf.active}


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


def folder_metrics_bulk(
    db: Session,
    folders: list[Node],
    *,
    view_filter: "ViewFilter | None" = None,
) -> dict[int, dict]:
    """Rollup over each folder's descendant files, for many folders in ONE query.

    For every folder in ``folders`` returns the total descendant-file count, the
    filtered file count/size (respecting ``view_filter``), and how many files are
    *effectively* marked no_transfer / processed (for the tri-state checkbox). All
    counts exclude sub-folders and the folder itself; the marked counts read the
    materialized ``<field>_eff`` columns, so a file under a marked folder counts
    even with no own value, with no recursive walk at read time.
    """
    if not folders:
        return {}
    f = aliased(Node)
    d = aliased(Node)
    is_file = d.is_dir.is_(False)
    join_cond = and_(
        d.dataset_id == f.dataset_id,
        _descendant_of_pred(f.mat_path, d.mat_path),
        d.id != f.id,
    )
    view_pred = view_filter.build(d) if view_filter is not None else None
    filtered = and_(is_file, view_pred) if view_pred is not None else is_file
    nt = and_(is_file, d.no_transfer_eff.is_(True))
    pc = and_(is_file, d.processed_eff.is_(True))

    stmt = (
        select(
            f.id.label("folder_id"),
            func.count().filter(is_file).label("total_files"),
            func.count().filter(filtered).label("filtered_file_count"),
            func.coalesce(func.sum(d.size_bytes).filter(filtered), 0).label("filtered_total_size"),
            func.count().filter(nt).label("no_transfer_marked"),
            func.count().filter(pc).label("processed_marked"),
        )
        .select_from(f)
        .join(d, join_cond, isouter=True)  # keep folders with zero descendant files
        .where(f.id.in_([n.id for n in folders]))
        .group_by(f.id)
    )
    out: dict[int, dict] = {}
    for row in db.execute(stmt).all():
        out[row.folder_id] = {
            "total_files": int(row.total_files),
            "filtered_file_count": int(row.filtered_file_count),
            "filtered_total_size": int(row.filtered_total_size),
            "no_transfer_marked": int(row.no_transfer_marked),
            "processed_marked": int(row.processed_marked),
        }
    return out


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
    view_filter: "ViewFilter | None" = None,
    search: str | None = None,
) -> list[dict]:
    """File-type histogram (count + total size) for files under ``node``.

    ``view_filter`` is the combined filter spec (type / last-accessed / flags /
    assignee / jira) so the breakdown matches the folder's filtered file count.
    """
    conds = [
        Node.dataset_id == node.dataset_id,
        Node.mat_path.like(f"{node.mat_path}%"),
        Node.id != node.id,
        Node.is_dir.is_(False),
    ]
    view_pred = view_filter.build(Node) if view_filter is not None else None
    if view_pred is not None:
        conds.append(view_pred)
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
    (so callers can reuse its shared effective-value CTE for the rollups).
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
    view_pred = filters["view_filter"].build(Node)
    if view_pred is not None:
        conds.append(view_pred)
    return conds, filters


def bulk_set_matching(
    db: Session, *, dataset_id: int, values: dict, actor: str | None = None, **filters
) -> tuple[int, list[int]]:
    """Apply ``values`` to every node matching the grid filter (all pages).

    Returns ``(count, node_ids)`` -- the caller needs ``node_ids`` (an
    arbitrary, possibly scattered set spanning the whole dataset, unlike the
    other bulk paths which are bounded to one folder's subtree) to scope the
    effective-column refresh to just what was touched.
    """
    conds, _ = grid_filter_conds(db, dataset_id, **filters)
    node_ids = [r[0] for r in db.execute(select(Node.id).where(and_(*conds))).all()]
    count = _apply_annotation_values(db, dataset_id, node_ids, values, actor)
    return count, node_ids
