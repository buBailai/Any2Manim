"""项目/版本/消息的持久化操作 + 文件目录约定（第六节）。

  data/projects/<id>/code/v*.py        代码快照（廉价，长期留）
  data/projects/<id>/thumbs/v*.png      缩略图（廉价，长期留）
  data/projects/<id>/previews/v*.mp4    低清预览（临时，可 GC）
  data/projects/<id>/exports/*.mp4      高清成片（用户保留）
"""
from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Any, Optional

from . import config, db


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def _rel(p: Path) -> str:
    """转成相对 data/ 的路径，供前端按 /media/<rel> 取用。"""
    return str(p.relative_to(config.DATA_DIR))


# ── 项目 ────────────────────────────────────────────────────
def create_project(title: str, subject: str = "", owner: str = "local") -> dict:
    pid = _id("proj")
    t = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO projects(id,title,subject,owner,current_version,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (pid, title, subject, owner, None, t, t))
    for sub in ("code", "thumbs", "previews", "exports"):
        (config.project_dir(pid) / sub).mkdir(parents=True, exist_ok=True)
    return get_project(pid)


def get_project(pid: str) -> Optional[dict]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return db.row_to_dict(row)


def list_projects(owner: str = "local", archived: bool = False) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE owner=? AND archived=? ORDER BY updated_at DESC",
            (owner, 1 if archived else 0)).fetchall()
    return [dict(r) for r in rows]


def delete_project(pid: str) -> None:
    """彻底删除项目：数据库各表 + 落盘产物 + 素材。"""
    with db.connect() as conn:
        for t in ("versions", "messages", "exports", "assets"):
            conn.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    shutil.rmtree(config.project_dir(pid), ignore_errors=True)
    shutil.rmtree(assets_dir(pid), ignore_errors=True)


def set_archived(pid: str, archived: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                     (1 if archived else 0, db.now(), pid))


def touch_project(pid: str, current_version: Optional[int] = None) -> None:
    with db.connect() as conn:
        if current_version is not None:
            conn.execute("UPDATE projects SET updated_at=?, current_version=? WHERE id=?",
                         (db.now(), current_version, pid))
        else:
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), pid))


# ── 版本 ────────────────────────────────────────────────────
def next_seq(pid: str) -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(seq) m FROM versions WHERE project_id=?",
                           (pid,)).fetchone()
    return (row["m"] or 0) + 1


def create_version(pid: str, seq: int, prompt: str) -> str:
    vid = _id("ver")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO versions(id,project_id,seq,prompt,status,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (vid, pid, seq, prompt, "pending", db.now()))
    return vid


def finish_version(pid: str, seq: int, *, status: str, code: str,
                   storyboard: str = "", thumb: Optional[Path] = None,
                   preview: Optional[Path] = None, heal_attempts: int = 0,
                   error: str = "") -> None:
    # 代码快照落盘
    if code:
        cf = config.project_dir(pid) / "code" / f"v{seq}.py"
        cf.write_text(code, encoding="utf-8")
    with db.connect() as conn:
        conn.execute(
            "UPDATE versions SET status=?,code=?,storyboard=?,thumb_path=?,"
            "preview_path=?,heal_attempts=?,error=? WHERE project_id=? AND seq=?",
            (status, code, storyboard,
             _rel(thumb) if thumb else None,
             _rel(preview) if preview else None,
             heal_attempts, error, pid, seq))


def delete_version(pid: str, seq: int) -> None:
    """删掉某个版本（用于「定向编辑没定位到改处」时清掉本轮空 pending 版，不留废记录）。"""
    with db.connect() as conn:
        conn.execute("DELETE FROM versions WHERE project_id=? AND seq=?", (pid, seq))
    try:
        (config.project_dir(pid) / "code" / f"v{seq}.py").unlink(missing_ok=True)
    except OSError:
        pass


def get_versions(pid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM versions WHERE project_id=? ORDER BY seq", (pid,)).fetchall()
    return [dict(r) for r in rows]


def get_version(pid: str, seq: int) -> Optional[dict]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM versions WHERE project_id=? AND seq=?",
                           (pid, seq)).fetchone()
    return db.row_to_dict(row)


def set_narration(pid: str, seq: int, text: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE versions SET narration=? WHERE project_id=? AND seq=?",
                     (text, pid, seq))


def current_code(pid: str) -> Optional[str]:
    proj = get_project(pid)
    if not proj or proj["current_version"] is None:
        return None
    v = get_version(pid, proj["current_version"])
    return v["code"] if v else None


def latest_code(pid: str) -> Optional[str]:
    """最近一版【有代码】的版本（含失败版）——失败后老师也能在这次尝试基础上继续改。

    必须排除刚为本轮新建、code 还为空的 pending 版本：_run_generation 是先 create_version
    （此时本轮 code 为空）再取 prior，若不排空会把本轮空版当成最近版 → prior=None →
    respond 退化成「无旧代码」直接整段重生成、还丢了原始主题 → 改个需求变成做无关新主题。
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT code FROM versions WHERE project_id=? AND code IS NOT NULL AND code!=''"
            " ORDER BY seq DESC LIMIT 1",
            (pid,)).fetchone()
    return (row["code"] if row and row["code"] else None)


# ── 消息 ────────────────────────────────────────────────────
def add_message(pid: str, role: str, content: str,
                version_seq: Optional[int] = None) -> dict:
    mid = _id("msg")
    t = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO messages(id,project_id,role,content,version_seq,created_at)"
            " VALUES(?,?,?,?,?,?)", (mid, pid, role, content, version_seq, t))
    return {"id": mid, "role": role, "content": content,
            "version_seq": version_seq, "created_at": t}


def first_user_prompt(pid: str) -> str:
    """项目的第一条用户消息 = 原始教学动画需求（写旁白的真正依据，排除后续改动指令）。"""
    for m in get_messages(pid):
        if m["role"] == "user":
            return m["content"]
    return ""


def latest_storyboard(pid: str) -> str:
    """最近一个非空分镜（定向编辑不产分镜，故回溯到上一次完整生成的分镜）。"""
    for v in reversed(get_versions(pid)):
        if v.get("storyboard"):
            return v["storyboard"]
    return ""


def get_messages(pid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE project_id=? ORDER BY created_at", (pid,)).fetchall()
    return [dict(r) for r in rows]


# ── 导出 ────────────────────────────────────────────────────
def add_asset(pid: str, kind: str, path: Path, orig_name: str, owner: str = "local") -> dict:
    aid = _id("asset")
    t = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO assets(id,owner,project_id,kind,path,orig_name,created_at)"
            " VALUES(?,?,?,?,?,?,?)", (aid, owner, pid, kind, str(path), orig_name, t))
    return {"id": aid, "kind": kind, "name": orig_name, "url": "/media/" + _rel(path), "created_at": t}


def get_assets(pid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM assets WHERE project_id=? ORDER BY created_at", (pid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        out.append({"id": d["id"], "kind": d["kind"], "name": d["orig_name"],
                    "url": "/media/" + str(Path(d["path"]).relative_to(config.DATA_DIR)),
                    "created_at": d["created_at"]})
    return out


def assets_dir(pid: str) -> Path:
    return config.ASSETS_DIR / pid


def delete_asset(aid: str) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT path FROM assets WHERE id=?", (aid,)).fetchone()
        if row:
            try:
                Path(row["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute("DELETE FROM assets WHERE id=?", (aid,))


def add_export(pid: str, seq: int, path: Path) -> dict:
    eid = _id("exp")
    t = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO exports(id,project_id,version_seq,path,created_at)"
            " VALUES(?,?,?,?,?)", (eid, pid, seq, _rel(path), t))
    return {"id": eid, "path": _rel(path), "version_seq": seq, "created_at": t}
