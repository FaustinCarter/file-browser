"""Dataset upload / list / delete endpoints."""
from __future__ import annotations

import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import csv_import, services
from ..database import get_db
from ..models import Annotation, Dataset, Node
from ..schemas import DatasetOut

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    return db.execute(select(Dataset).order_by(Dataset.created_at.desc())).scalars().all()


@router.post("", response_model=DatasetOut)
def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    # A plain (sync) endpoint runs in a threadpool, so the multi-minute import of
    # a large file doesn't block the event loop. Stream the upload to a temp file
    # instead of reading it all into RAM, then parse from disk.
    ds_name = (name or "").strip() or (file.filename or "dataset").rsplit(".", 1)[0]
    tmp = tempfile.NamedTemporaryFile(prefix="fb_upload_", suffix=".csv", delete=False)
    try:
        shutil.copyfileobj(file.file, tmp, length=1024 * 1024)
        tmp.flush()
        tmp.close()
        dataset = csv_import.import_csv_path(
            db, name=ds_name, filename=file.filename or "upload.csv", path=tmp.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return dataset


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db.execute(delete(Annotation).where(Annotation.dataset_id == dataset_id))
    db.execute(delete(Node).where(Node.dataset_id == dataset_id))
    db.delete(ds)
    db.commit()
    return {"deleted": dataset_id}


@router.get("/{dataset_id}/distinct/{field}")
def distinct_values(dataset_id: int, field: str, db: Session = Depends(get_db)):
    """Distinct assigned values for an editable field (filter dropdowns)."""
    if not db.get(Dataset, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset not found")
    return {"values": services.distinct_values(db, dataset_id, field)}


@router.get("/{dataset_id}/file-types")
def file_types(dataset_id: int, db: Session = Depends(get_db)):
    """Distinct file types in the dataset with counts (for the filter UI)."""
    if not db.get(Dataset, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset not found")
    rows = db.execute(
        select(Node.file_type, func.count(Node.id))
        .where(Node.dataset_id == dataset_id, Node.is_dir.is_(False))
        .group_by(Node.file_type)
        .order_by(func.count(Node.id).desc())
    ).all()
    return [{"file_type": r[0], "count": int(r[1])} for r in rows]
