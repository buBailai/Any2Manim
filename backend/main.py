"""Any2Manim 后端入口（FastAPI）。个人版单机 MVP。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, store
from .jobs import broker, queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    queue.start()
    yield


app = FastAPI(title="Any2Manim", lifespan=lifespan)


# ── 请求体 ──────────────────────────────────────────────────
class ConfigIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = ""


class ProjectIn(BaseModel):
    title: str = "未命名动画"
    subject: str = ""


class MessageIn(BaseModel):
    prompt: str


class ExportIn(BaseModel):
    seq: int
    formats: list[str] = ["mp4"]      # 子集 of mp4/gif/cover
    quality: str = "h"                # l/m/h/k
    voiceover: bool = False
    voice: str = ""
    rate: str = "+0%"                 # edge-tts 语速，如 -10% / +20%
    subtitle: str = "none"            # none | burn | srt
    cover_time: float = 0.0           # 封面取第几秒的画面（对应预览进度条位置）


class RevertIn(BaseModel):
    seq: int


class ArchiveIn(BaseModel):
    archived: bool


class NarrationIn(BaseModel):
    text: str


# ── 配置（BYO-Key）─────────────────────────────────────────
@app.get("/api/providers")
def get_providers():
    from . import providers
    return providers.public_list()


@app.get("/api/examples")
def get_examples():
    from . import examples
    return examples.public_list()


@app.get("/api/voices")
def get_voices():
    from . import tts
    return {"voices": tts.VOICES, "default": tts.DEFAULT_VOICE}


# ── 版本 / 更新日志（读 CHANGELOG.md，发版只改文档不动代码）──────
import re

_CHANGELOG_PATH = config.APP_DIR / "CHANGELOG.md"
_VER_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]")
_changelog_cache = {"mtime": -1.0, "version": "0.0.0", "markdown": ""}


def _read_changelog() -> dict:
    """按 mtime 缓存解析 CHANGELOG：返回最新发布版本号 + 全文 markdown。改后下次请求即生效。"""
    try:
        mtime = _CHANGELOG_PATH.stat().st_mtime
    except OSError:
        return {"version": _changelog_cache["version"], "markdown": _changelog_cache["markdown"]}
    if mtime != _changelog_cache["mtime"]:
        text = _CHANGELOG_PATH.read_text(encoding="utf-8")
        version = next((m.group(1) for ln in text.splitlines()
                        if (m := _VER_RE.match(ln.strip()))), "0.0.0")
        _changelog_cache.update(mtime=mtime, version=version, markdown=text)
    return {"version": _changelog_cache["version"], "markdown": _changelog_cache["markdown"]}


@app.get("/api/changelog")
def get_changelog():
    return _read_changelog()


@app.get("/api/config")
def get_config():
    from .llm import from_config
    cfg = {}
    if config.CONFIG_PATH.exists():
        cfg = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    demo = getattr(from_config(cfg), "demo", True)
    return {"configured": not demo,
            "provider": cfg.get("provider", ""),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "demo": demo}


@app.post("/api/config")
def set_config(c: ConfigIn):
    config.ensure_dirs()
    cfg = c.model_dump()
    # 合并：Key 留空时保留已存的（编辑模型/URL 不必重填 Key）
    if not cfg.get("api_key") and config.CONFIG_PATH.exists():
        old = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        if old.get("api_key"):
            cfg["api_key"] = old["api_key"]
    config.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    from .llm import from_config
    return {"ok": True, "demo": getattr(from_config(cfg), "demo", True)}


@app.post("/api/config/test")
def test_config(c: ConfigIn):
    """连通性自检：用给定（或已存）配置发一条极小请求，回报成功/具体错误。"""
    from .llm import from_config, LLMError
    cfg = c.model_dump()
    old = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8")) \
        if config.CONFIG_PATH.exists() else {}
    # 表单空字段用已存值补齐（Key 留空仍能测；只改模型也能测新模型）
    for k in ("base_url", "api_key", "model", "provider"):
        if not cfg.get(k) and old.get(k):
            cfg[k] = old[k]
    llm = from_config(cfg)
    if getattr(llm, "demo", False):
        return {"ok": False, "message": "未填 API Key —— 当前是演示模式（用内置范例，不调大模型）"}
    try:
        out = llm.complete("你是助手。", "只回复两个字：连通", temperature=0)
        return {"ok": True, "message": "连接成功 ✓", "model": llm.name,
                "sample": (out or "").strip()[:40]}
    except LLMError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"连接失败：{e}"}


# ── 项目 ────────────────────────────────────────────────────
@app.get("/api/projects")
def list_projects(archived: bool = False):
    return store.list_projects(archived=archived)


@app.post("/api/projects")
def create_project(p: ProjectIn):
    return store.create_project(p.title, p.subject)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    proj = store.get_project(pid)
    if not proj:
        raise HTTPException(404, "项目不存在")
    return {"project": proj,
            "versions": store.get_versions(pid),
            "messages": store.get_messages(pid)}


_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@app.post("/api/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...)):
    if not store.get_project(pid):
        raise HTTPException(404, "项目不存在")
    name = Path(file.filename or "asset").name        # 防目录穿越
    ext = Path(name).suffix.lower()
    if ext == ".svg":
        kind = "svg"
    elif ext in _IMG_EXT:
        kind = "image"
    else:
        raise HTTPException(400, "只支持图片（png/jpg/webp/gif）或 svg")
    adir = store.assets_dir(pid)
    adir.mkdir(parents=True, exist_ok=True)
    dest = adir / name
    dest.write_bytes(await file.read())
    return store.add_asset(pid, kind, dest, name)


@app.get("/api/projects/{pid}/assets")
def list_assets(pid: str):
    return store.get_assets(pid)


@app.delete("/api/projects/{pid}/assets/{aid}")
def delete_asset(pid: str, aid: str):
    store.delete_asset(aid)
    return {"ok": True}


@app.get("/api/projects/{pid}/version/{seq}")
def get_version(pid: str, seq: int):
    v = store.get_version(pid, seq)
    if not v:
        raise HTTPException(404, "版本不存在")
    return v


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    if not store.get_project(pid):
        raise HTTPException(404, "项目不存在")
    store.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/archive")
def post_archive(pid: str, a: ArchiveIn):
    if not store.get_project(pid):
        raise HTTPException(404, "项目不存在")
    store.set_archived(pid, a.archived)
    return {"ok": True, "archived": a.archived}


@app.post("/api/projects/{pid}/message")
async def post_message(pid: str, m: MessageIn):
    proj = store.get_project(pid)
    if not proj:
        raise HTTPException(404, "项目不存在")
    if proj.get("archived"):
        raise HTTPException(409, "项目已归档（只读），恢复到项目列表后才能编辑")
    store.add_message(pid, "user", m.prompt)
    pos = await queue.submit_generation(pid, m.prompt)
    return {"ok": True, "queue_position": pos}


@app.post("/api/projects/{pid}/version/{seq}/narration")
def save_narration(pid: str, seq: int, n: NarrationIn):
    v = store.get_version(pid, seq)
    if not v:
        raise HTTPException(404, "版本不存在")
    proj = store.get_project(pid)
    if proj and proj.get("archived"):
        raise HTTPException(409, "项目已归档（只读）")
    store.set_narration(pid, seq, n.text.strip())
    return {"ok": True}


@app.post("/api/projects/{pid}/version/{seq}/narration/generate")
async def generate_narration(pid: str, seq: int):
    from . import engine, tts
    from .jobs import _load_llm
    v = store.get_version(pid, seq)
    if not v:
        raise HTTPException(404, "版本不存在")
    dur = 0.0
    if v.get("preview_path"):
        p = config.DATA_DIR / v["preview_path"]
        if p.exists():
            dur = await tts._duration(p)
    topic = store.first_user_prompt(pid) or v.get("prompt") or ""      # 原始教学需求
    storyboard = store.latest_storyboard(pid) or v.get("storyboard") or ""
    text = engine.narrate(topic, storyboard, _load_llm(),
                          code=v.get("code") or "", total_dur=dur)
    if text:
        store.set_narration(pid, seq, text)
    return {"text": text, "storyboard": storyboard}


@app.post("/api/projects/{pid}/export")
async def post_export(pid: str, e: ExportIn):
    if not store.get_version(pid, e.seq):
        raise HTTPException(404, "版本不存在")
    fmts = [f for f in e.formats if f in ("mp4", "gif", "cover")] or ["mp4"]
    quality = e.quality if e.quality in ("l", "m", "h", "k") else "h"
    vo = {"enabled": e.voiceover and "mp4" in fmts, "voice": e.voice,
          "rate": e.rate, "subtitle": e.subtitle if e.subtitle in ("none", "burn", "srt") else "none"}
    pos = await queue.submit_export(pid, e.seq, fmts, quality, vo,
                                    cover_time=max(0.0, e.cover_time))
    return {"ok": True, "queue_position": pos}


@app.post("/api/projects/{pid}/revert")
def post_revert(pid: str, r: RevertIn):
    proj = store.get_project(pid)
    if proj and proj.get("archived"):
        raise HTTPException(409, "项目已归档（只读），恢复后才能回退版本")
    v = store.get_version(pid, r.seq)
    if not v or v["status"] != "ok":
        raise HTTPException(400, "该版本不可用")
    store.touch_project(pid, current_version=r.seq)
    return {"ok": True, "current_version": r.seq}


# ── SSE ─────────────────────────────────────────────────────
@app.get("/api/projects/{pid}/events")
async def events(pid: str, request: Request):
    q = broker.subscribe(pid)

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(pid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── 静态资源 ────────────────────────────────────────────────
config.ensure_dirs()
app.mount("/media", StaticFiles(directory=str(config.DATA_DIR)), name="media")


@app.get("/")
def index():
    return FileResponse(str(config.FRONTEND_DIR / "index.html"))


app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR)), name="frontend")
