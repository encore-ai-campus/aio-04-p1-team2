"""관찰 기록과 게시글 사진을 읽기 권한에 따라 제공해요."""
from __future__ import annotations

import pathlib
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core.security import optional_user
from backend.db import get_session
from backend.models import Observation, Post, User
from backend.routers.community import readable_post

router = APIRouter(tags=["사진"])


# 2026.11.12 sunghoon 사진 소유자 또는 게시글 읽기 권한이 있는 사용자에게만 파일 제공
@router.get("/files/{filename}")
def photo(filename: str, user: User | None = Depends(optional_user), session: Session = Depends(get_session)):
    if not re.fullmatch(r"(?:ph|pp)_[a-z0-9]{10}\.jpg", filename):
        raise HTTPException(404, "사진을 찾을 수 없어요.")
    owned = bool(user and session.exec(select(Observation).where(
        Observation.photo_path == filename, Observation.user_id == user.id)).first())
    if not owned:
        posts = session.exec(select(Post).where(Post.photo_path == filename)).all()
        for post in posts:
            try:
                readable_post(post.id, user, session)
                break
            except HTTPException:
                continue
        else:
            raise HTTPException(404, "사진을 찾을 수 없어요.")
    root = pathlib.Path(get_settings().upload_dir).resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "사진을 찾을 수 없어요.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
