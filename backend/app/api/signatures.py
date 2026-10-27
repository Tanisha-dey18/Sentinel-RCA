from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Signature
from app.schemas import SignatureCreate, SignatureOut, SignatureTestIn, SignatureTestOut, SignatureUpdate

router = APIRouter(prefix="/api/signatures", tags=["signatures"])


@router.get("", response_model=list[SignatureOut])
def list_signatures(db: Session = Depends(get_db)):
    return db.scalars(select(Signature).order_by(Signature.category, Signature.key)).all()


@router.post("", response_model=SignatureOut, status_code=201)
def create_signature(body: SignatureCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Signature).where(Signature.key == body.key)):
        raise HTTPException(status_code=409, detail=f"signature '{body.key}' already exists")
    sig = Signature(**body.model_dump(), builtin=False)
    db.add(sig)
    db.commit()
    return sig


@router.put("/{key}", response_model=SignatureOut)
def update_signature(key: str, body: SignatureUpdate, db: Session = Depends(get_db)):
    sig = db.scalar(select(Signature).where(Signature.key == key))
    if sig is None:
        raise HTTPException(status_code=404, detail="signature not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(sig, k, v)
    db.commit()
    return sig


@router.delete("/{key}", status_code=204)
def delete_signature(key: str, db: Session = Depends(get_db)):
    sig = db.scalar(select(Signature).where(Signature.key == key))
    if sig is None:
        raise HTTPException(status_code=404, detail="signature not found")
    if sig.builtin:
        raise HTTPException(status_code=400, detail="built-in signatures can be disabled but not deleted")
    db.delete(sig)
    db.commit()


@router.post("/test", response_model=SignatureTestOut, summary="Test a regex against sample messages")
def test_signature(body: SignatureTestIn):
    try:
        rx = re.compile(body.pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise HTTPException(status_code=422, detail=f"invalid regular expression: {exc}") from exc
    results = []
    for m in body.messages:
        hit = rx.search(m)
        results.append({"message": m, "matched": bool(hit), "span": list(hit.span()) if hit else None,
                        "match": hit.group(0) if hit else None})
    return SignatureTestOut(results=results)
