"""Helpers that turn ORM Nodes into API NodeOut objects."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services
from .models import Node
from .schemas import EffectiveAnnotation, NodeOut


def _children_presence(db: Session, node_ids: list[int]) -> set[int]:
    """Return the subset of ``node_ids`` that have at least one child."""
    if not node_ids:
        return set()
    rows = db.execute(
        select(Node.parent_id)
        .where(Node.parent_id.in_(node_ids))
        .group_by(Node.parent_id)
    ).all()
    return {r[0] for r in rows}


def build_node_outs(
    db: Session,
    nodes: list[Node],
    *,
    types: list[str] | None = None,
    accessed_after: date | None = None,
    accessed_before: date | None = None,
    with_folder_stats: bool = True,
) -> list[NodeOut]:
    if not nodes:
        return []
    eff_map = services.resolve_effective(db, nodes)
    has_kids = _children_presence(db, [n.id for n in nodes])

    out: list[NodeOut] = []
    for n in nodes:
        info = eff_map[n.id]
        eff = info["effective"]
        src = info["source"]
        own = info["own"]
        inherited = [f for f in services.ANNOTATION_FIELDS
                    if src[f] is not None and src[f] != n.id]

        item = NodeOut(
            id=n.id,
            dataset_id=n.dataset_id,
            parent_id=n.parent_id,
            depth=n.depth,
            name=n.name,
            full_path=n.full_path,
            is_dir=n.is_dir,
            size_raw=n.size_raw,
            size_bytes=n.size_bytes,
            allocated_raw=n.allocated_raw,
            allocated_bytes=n.allocated_bytes,
            files_count=n.files_count,
            folders_count=n.folders_count,
            pct_parent_raw=n.pct_parent_raw,
            pct_parent=n.pct_parent,
            last_modified=n.last_modified,
            last_accessed=n.last_accessed,
            owner=n.owner,
            file_type=n.file_type,
            dir_level=n.dir_level,
            has_children=n.id in has_kids,
            effective=EffectiveAnnotation(**eff),
            own=EffectiveAnnotation(**own),
            inherited_fields=inherited,
        )
        if n.is_dir and with_folder_stats:
            stats = services.folder_stats(
                db, n, types=types, accessed_after=accessed_after,
                accessed_before=accessed_before,
            )
            item.filtered_file_count = stats["file_count"]
            item.filtered_total_size = stats["total_size"]
        out.append(item)
    return out
