"""The gun's remote debug link (Steve, 2026-10-07: "add a connection to
the app again so you can debug in real time").

Off unless the gun turns on Settings > DEVELOPER > Remote debugging.
The gun posts its log lines (app events, camera steps, crash traces,
main-thread stalls) every couple of seconds, and polls for one queued
command at a time: ping, state, views, logcat, shot (a screenshot of
whatever screen is up, camera picture included), camlog,
restart_camera. Everything lands in the database except screenshots,
which go to disk. Both tables live in the database (two gunicorn
workers: an in-memory queue lost commands before).
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.boxphotos import photo_dir
from app.database import get_session
from app.models import DevCommand, DevLogLine

router = APIRouter(prefix="/api/devlog", dependencies=[Depends(require_user)])

COMMANDS = ("ping", "state", "views", "logcat", "shot", "camlog",
            "restart_camera")
KEEP_LINES = 5000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _shot_dir() -> Path:
    d = photo_dir().parent / "devshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


class LinesIn(BaseModel):
    device: str = Field(default="C72", max_length=100)
    lines: list[str] = Field(min_length=1, max_length=500)


@router.post("/lines")
def post_lines(payload: LinesIn, session: Session = Depends(get_session)):
    for line in payload.lines:
        session.add(DevLogLine(device=payload.device, line=line[:8000]))
    session.flush()
    newest = session.scalar(select(DevLogLine.id).order_by(DevLogLine.id.desc()).limit(1))
    if newest and newest > KEEP_LINES and newest % 50 < len(payload.lines):
        session.execute(delete(DevLogLine).where(DevLogLine.id <= newest - KEEP_LINES))
    session.commit()
    return {"ok": True}


@router.get("/lines")
def get_lines(after: int = 0, device: str | None = None, limit: int = 300,
              session: Session = Depends(get_session)):
    q = select(DevLogLine).where(DevLogLine.id > after)
    if device:
        q = q.where(DevLogLine.device == device)
    rows = session.scalars(q.order_by(DevLogLine.id.desc())
                           .limit(max(1, min(limit, 2000)))).all()
    rows.reverse()
    return {"lines": [{"id": r.id, "device": r.device, "line": r.line,
                       "at": r.created_at.isoformat() if r.created_at else None}
                      for r in rows]}


class CommandIn(BaseModel):
    device: str = Field(default="*", max_length=100)
    cmd: str = Field(max_length=40)


def _cmd_dict(row: DevCommand) -> dict:
    return {"id": row.id, "device": row.device, "cmd": row.cmd,
            "taken_by": row.taken_by,
            "taken_at": row.taken_at.isoformat() if row.taken_at else None,
            "done_at": row.done_at.isoformat() if row.done_at else None,
            "result": row.result, "has_shot": row.has_shot}


@router.post("/commands")
def queue_command(payload: CommandIn, session: Session = Depends(get_session)):
    if payload.cmd not in COMMANDS:
        raise HTTPException(400, f"Unknown command. Known: {', '.join(COMMANDS)}.")
    row = DevCommand(device=payload.device, cmd=payload.cmd)
    session.add(row)
    session.commit()
    return {"command": _cmd_dict(row)}


@router.get("/commands/next")
def next_command(device: str = "C72", session: Session = Depends(get_session)):
    """The gun's poll: claim the oldest fresh command meant for it."""
    fresh = _now() - timedelta(minutes=10)
    row = session.scalar(
        select(DevCommand)
        .where(DevCommand.taken_at.is_(None), DevCommand.created_at >= fresh,
               or_(DevCommand.device == device, DevCommand.device == "*"))
        .order_by(DevCommand.id)
        .limit(1)
    )
    if row is None:
        return {"command": None}
    row.taken_at = _now()
    row.taken_by = device
    session.commit()
    return {"command": {"id": row.id, "cmd": row.cmd}}


class ResultIn(BaseModel):
    result: str | None = Field(default=None, max_length=200000)
    shot_b64: str | None = None


@router.post("/commands/{cmd_id}/result")
def command_result(cmd_id: int, payload: ResultIn,
                   session: Session = Depends(get_session)):
    row = session.get(DevCommand, cmd_id)
    if row is None:
        raise HTTPException(404, "No such command.")
    if payload.shot_b64:
        try:
            data = base64.b64decode(payload.shot_b64)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "The screenshot didn't arrive intact.")
        (_shot_dir() / f"{row.id}.jpg").write_bytes(data)
        row.has_shot = True
    row.result = payload.result
    row.done_at = _now()
    session.commit()
    return {"ok": True}


@router.get("/commands/{cmd_id}")
def get_command(cmd_id: int, session: Session = Depends(get_session)):
    row = session.get(DevCommand, cmd_id)
    if row is None:
        raise HTTPException(404, "No such command.")
    return {"command": _cmd_dict(row)}


@router.get("/commands/{cmd_id}/shot")
def get_shot(cmd_id: int):
    path = _shot_dir() / f"{cmd_id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "No screenshot for that command.")
    return FileResponse(path, media_type="image/jpeg")
