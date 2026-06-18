"""Database models.

Design notes
------------
* One row per filesystem object, scoped to a ``dataset`` (one uploaded CSV).
* The original CSV columns live on ``Node`` and are treated as **read only** by
  the API – there is no endpoint that mutates them.
* User-editable data lives in a separate ``Annotation`` row (1:1 with a node).
  Every annotation field is nullable; ``NULL`` means "inherit from the nearest
  ancestor that has a value". This gives us the requested *inherited + override*
  behaviour for free and means marking a folder never rewrites its children.
* Hierarchy is stored as a materialized path of ids (``mat_path`` e.g.
  ``/1/5/23/``). Descendant queries are a single ``LIKE '/1/5/23/%'`` and
  ancestor lookups just split the string. This is portable (no ltree
  extension) and indexes well on Postgres at millions of rows.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Hierarchy
    mat_path: Mapped[str] = mapped_column(Text, nullable=False)  # "/1/5/23/"
    depth: Mapped[int] = mapped_column(Integer, default=0)

    # ---- Raw, read-only columns from the CSV ----
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_path: Mapped[str] = mapped_column(Text, nullable=False)
    path_key: Mapped[str] = mapped_column(Text, nullable=False)  # normalised lookup key
    is_dir: Mapped[bool] = mapped_column(Boolean, default=False)

    size_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allocated_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    allocated_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    files_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    folders_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    pct_parent_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    pct_parent: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_modified: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_accessed: Mapped[date | None] = mapped_column(Date, nullable=True)

    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # "PPTX File"
    dir_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    annotation: Mapped["Annotation"] = relationship(
        back_populates="node", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_nodes_dataset", "dataset_id"),
        Index("ix_nodes_parent", "parent_id"),
        # Prefix index for descendant LIKE queries (text_pattern_ops on PG).
        Index(
            "ix_nodes_matpath",
            "dataset_id",
            "mat_path",
            postgresql_ops={"mat_path": "text_pattern_ops"},
        ),
        Index("ix_nodes_type", "dataset_id", "file_type"),
        Index("ix_nodes_pathkey", "dataset_id", "path_key"),
        Index("ix_nodes_accessed", "dataset_id", "last_accessed"),
    )


class Annotation(Base):
    __tablename__ = "annotations"

    node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # NULL == inherit from nearest ancestor with a value.
    processed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    no_transfer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    node: Mapped[Node] = relationship(back_populates="annotation")

    __table_args__ = (
        Index("ix_annotations_dataset", "dataset_id"),
        Index("ix_annotations_jira", "dataset_id", "jira_ticket"),
    )
