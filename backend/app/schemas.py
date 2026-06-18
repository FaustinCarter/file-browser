"""Pydantic response/request models for the API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    filename: str
    row_count: int
    created_at: datetime | None = None


class EffectiveAnnotation(BaseModel):
    processed: bool | None = None
    no_transfer: bool | None = None
    target_location: str | None = None
    jira_ticket: str | None = None
    comment: str | None = None
    user_name: str | None = None


class NodeOut(BaseModel):
    id: int
    dataset_id: int
    parent_id: int | None
    depth: int
    name: str
    full_path: str
    is_dir: bool
    size_raw: str | None
    size_bytes: int | None
    allocated_raw: str | None
    allocated_bytes: int | None
    files_count: int | None
    folders_count: int | None
    pct_parent_raw: str | None
    pct_parent: float | None
    last_modified: date | None
    last_accessed: date | None
    owner: str | None
    file_type: str | None
    dir_level: int | None
    has_children: bool = False

    # annotation view
    effective: EffectiveAnnotation | None = None
    own: EffectiveAnnotation | None = None
    inherited_fields: list[str] = []  # fields whose effective value is inherited

    # folder aggregates (respecting active filters), only for directories
    filtered_file_count: int | None = None
    filtered_total_size: int | None = None
    # folder boolean rollups over descendant files (for tri-state checkboxes)
    total_files: int | None = None
    no_transfer_marked: int | None = None
    processed_marked: int | None = None


class TreeChildrenOut(BaseModel):
    parent_id: int | None
    children: list[NodeOut]


class TypeBreakdownRow(BaseModel):
    file_type: str | None
    count: int
    total_size: int


class CountsOut(BaseModel):
    file_count: int
    folder_count: int


class FolderStatsOut(BaseModel):
    file_count: int
    total_size: int


class AnnotationUpdate(BaseModel):
    # Use a sentinel-free approach: only fields explicitly provided are applied.
    processed: bool | None = None
    no_transfer: bool | None = None
    target_location: str | None = None
    jira_ticket: str | None = None
    comment: str | None = None
    user_name: str | None = None


class FolderFlagUpdate(BaseModel):
    """Set/clear a rollup boolean on a folder.

    With no filter the whole subtree is affected (descendant overrides are
    cleared and the folder's own value is set). With a type/last-accessed filter
    the action is scoped to the matching files only, leaving the folder's own
    value untouched (so it stays unchecked/indeterminate until all files match).
    """
    field: str  # "no_transfer" or "processed"
    value: bool | None  # True = mark, None = clear/unmark
    types: list[str] | None = None
    accessed_after: date | None = None
    accessed_before: date | None = None


class BulkAnnotationUpdate(BaseModel):
    node_id: int  # folder (or any node) to scope under
    include_self: bool = True
    files_only: bool = False
    # optional filters to narrow which descendants are stamped
    types: list[str] | None = None
    accessed_after: date | None = None
    accessed_before: date | None = None
    values: AnnotationUpdate


class FolderTypeCountRequest(BaseModel):
    """Answers: 'how many <type> files are in each of these folders?'"""
    node_ids: list[int]
    types: list[str] | None = None
    accessed_after: date | None = None
    accessed_before: date | None = None
