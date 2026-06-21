"""有限 worker 的渲染任务队列 + SSE 事件广播（第三节）。

个人版 RENDER_WORKERS=1（串行不卡死）；校园版改大即变 N worker，渲染逻辑不变。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from . import config, engine, render, store, tts
from .llm import BaseLLM, from_config


# ── SSE 广播 ────────────────────────────────────────────────
class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, pid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(pid, set()).add(q)
        return q

    def unsubscribe(self, pid: str, q: asyncio.Queue) -> None:
        self._subs.get(pid, set()).discard(q)

    async def publish(self, pid: str, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(pid, set())):
            await q.put(event)


broker = Broker()


def _media_url(p: Optional[Path]) -> Optional[str]:
    if not p:
        return None
    return "/media/" + str(p.relative_to(config.DATA_DIR))


def _safe_base(pid: str) -> str:
    """用项目名做下载文件名前缀（清掉文件系统/下载非法字符），取不到就退回 pid。"""
    proj = store.get_project(pid)
    title = (proj.get("title") if proj else "") or ""
    base = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title).strip().strip(".")
    base = re.sub(r"\s+", "_", base)[:60]
    return base or pid


def _dl_name(pid: str, seq: int, ext: str, suffix: str = "") -> str:
    """友好下载名：项目名_v{seq}[_后缀].{扩展名}。"""
    return f"{_safe_base(pid)}_v{seq}{suffix}.{ext}"


def _prepare_assets(pid: str) -> list[str]:
    """设置渲染子进程的素材目录环境变量；返回素材名列表（喂给 codegen 提示）。

    注意：依赖个人版"单 worker 串行"——os.environ 是进程级共享，并发渲染会串。
    校园版（N worker）须改为按子进程传 env。
    """
    os.environ["A2M_ASSET_DIR"] = str(store.assets_dir(pid))
    names = []
    for a in store.get_assets(pid):
        if a["name"] not in names:
            names.append(a["name"])
    return names


def _load_llm() -> BaseLLM:
    cfg = None
    if config.CONFIG_PATH.exists():
        try:
            cfg = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = None
    return from_config(cfg)


# ── 生成管线 ────────────────────────────────────────────────
async def _run_generation(pid: str, prompt: str) -> None:
    llm = _load_llm()
    seq = store.next_seq(pid)
    store.create_version(pid, seq, prompt)

    async def emit(ev: str, **data: Any) -> None:
        await broker.publish(pid, {"type": ev, "seq": seq, **data})

    await emit("version_start", demo=llm.demo)
    prior = store.latest_code(pid)   # 含失败版：失败后老师可在这次尝试基础上继续改
    asset_names = _prepare_assets(pid)

    result = await engine.respond(prompt, llm, emit, prior_code=prior,
                                  asset_names=asset_names)

    if getattr(result, "warnings", None):
        print("[教学检查] " + " | ".join(result.warnings), file=sys.stderr, flush=True)

    if not result.ok:
        store.finish_version(pid, seq, status="failed", code=result.code or "",
                             storyboard=result.storyboard,
                             heal_attempts=result.attempts, error=result.error)
        msg = result.error or "生成失败"
        store.add_message(pid, "ai", msg, version_seq=seq)
        await emit("failed", error=msg, env_missing=result.env_missing,
                   code=result.code or "")
        return

    pdir = config.project_dir(pid)
    # 缩略图：秒出首帧给即时反馈
    await emit("rendering", stage="thumb")
    thumb = pdir / "thumbs" / f"v{seq}.png"
    tr = await render.thumbnail(result.code, thumb)
    if tr.ok:
        await emit("thumb_ready", thumb_url=_media_url(thumb))

    # 低清预览
    await emit("rendering", stage="preview")
    prev = pdir / "previews" / f"v{seq}.mp4"
    pr = await render.preview(result.code, prev)

    if not pr.ok:
        store.finish_version(pid, seq, status="failed", code=result.code,
                             storyboard=result.storyboard,
                             thumb=thumb if tr.ok else None,
                             heal_attempts=result.attempts,
                             error="预览渲染失败：" + (pr.traceback[:120] or "未知"))
        store.add_message(pid, "ai", "预览渲染失败，可换个说法重试。", version_seq=seq)
        await emit("failed", error="预览渲染失败", code=result.code or "")
        return

    store.finish_version(pid, seq, status="ok", code=result.code,
                         storyboard=result.storyboard,
                         thumb=thumb if tr.ok else None, preview=prev,
                         heal_attempts=result.attempts)
    store.touch_project(pid, current_version=seq)

    note = "已生成动画并渲染出预览。" if result.attempts == 0 else \
           f"已生成动画（自动修正 {result.attempts} 次后渲染成功）。"
    store.add_message(pid, "ai", note, version_seq=seq)
    await emit("preview_ready", thumb_url=_media_url(thumb) if tr.ok else None,
               preview_url=_media_url(prev), attempts=result.attempts,
               demo=llm.demo)


_QLABEL = {"l": "480p", "m": "720p", "h": "1080p", "k": "4K"}


async def _video_duration(v: dict) -> float:
    """取该版本预览视频的真实时长（喂给旁白生成做节奏对齐）。"""
    if v.get("preview_path"):
        p = config.DATA_DIR / v["preview_path"]
        if p.exists():
            return await tts._duration(p)
    return 0.0


async def _ensure_narration(pid: str, seq: int, v: dict) -> str:
    if v.get("narration"):
        return v["narration"]
    dur = await _video_duration(v)
    topic = store.first_user_prompt(pid) or v.get("prompt") or ""      # 原始教学需求，排除改动指令
    storyboard = store.latest_storyboard(pid) or v.get("storyboard") or ""
    text = engine.narrate(topic, storyboard, _load_llm(),
                          code=v.get("code") or "", total_dur=dur)
    if text:
        store.set_narration(pid, seq, text)
    return text


async def _run_export(pid: str, seq: int, formats: list[str], quality: str,
                      vo: dict, cover_time: float = 0.0) -> None:
    v = store.get_version(pid, seq)
    if not v or not v["code"]:
        await broker.publish(pid, {"type": "export_failed", "error": "无可导出代码"})
        return
    exdir = config.project_dir(pid) / "exports"
    products: list[dict] = []
    _prepare_assets(pid)              # 导出渲染同样需要素材目录
    await broker.publish(pid, {"type": "exporting", "seq": seq})

    if "mp4" in formats:
        d = exdir / f"v{seq}_{quality}.mp4"
        r = await render.export(v["code"], d, quality=quality)
        if not r.ok:
            await broker.publish(pid, {"type": "export_failed", "seq": seq,
                                       "error": r.traceback[:160]})
            return
        final, label = d, f"高清视频 {_QLABEL.get(quality, quality)}"

        if vo.get("enabled"):
            await broker.publish(pid, {"type": "voicing", "seq": seq})
            fb = config.ffmpeg_bins()
            print(f"[配音] ffmpeg={fb[0]}", file=sys.stderr, flush=True)
            narration = await _ensure_narration(pid, seq, v)
            warn = None
            if not narration:
                warn = "没有解说词可配音"
            else:
                mp3 = exdir / f"v{seq}.mp3"
                if not await tts.synth(narration, mp3, voice=vo.get("voice", tts.DEFAULT_VOICE),
                                       rate=vo.get("rate", "+0%")):
                    warn = "配音合成失败（edge-tts，需联网；反复失败可能被网络拦截）"
                else:
                    srt = exdir / f"v{seq}.srt"
                    dur = await tts._duration(mp3)
                    has_srt = tts.build_srt(narration, dur, srt)
                    voiced = exdir / f"v{seq}_{quality}_voiced.mp4"
                    if not await tts.mux_audio(d, mp3, voiced):
                        warn = "音轨合成失败（ffmpeg 未就绪？看控制台 [配音] 日志）"
                    else:
                        final = voiced
                        label = f"高清视频 {_QLABEL.get(quality, quality)} · 配音"
                        sub = vo.get("subtitle")
                        if sub == "burn" and has_srt:
                            burned = exdir / f"v{seq}_{quality}_sub.mp4"
                            if await tts.burn_subtitles(voiced, srt, burned):
                                final = burned
                                label += "·字幕"
                            else:   # 本机 ffmpeg 无 libass → 退化为外挂字幕
                                products.append({"kind": "srt", "label": "字幕文件 SRT（烧录不可用，改外挂）", "url": _media_url(srt), "filename": _dl_name(pid, seq, "srt")})
                        elif sub == "srt" and has_srt:
                            products.append({"kind": "srt", "label": "字幕文件 SRT", "url": _media_url(srt), "filename": _dl_name(pid, seq, "srt")})
            if warn:
                print(f"[配音] 跳过：{warn}", file=sys.stderr, flush=True)
                await broker.publish(pid, {"type": "voice_warn", "seq": seq, "warn": warn})

        store.add_export(pid, seq, final)
        products.append({"kind": "mp4", "label": label, "url": _media_url(final),
                         "filename": _dl_name(pid, seq, "mp4")})

    if "gif" in formats:
        d = exdir / f"v{seq}.gif"
        r = await render.export_gif(v["code"], d)
        if r.ok:
            products.append({"kind": "gif", "label": "GIF 480p", "url": _media_url(d),
                             "filename": _dl_name(pid, seq, "gif")})

    if "cover" in formats:
        d = exdir / f"v{seq}_cover.png"
        # 封面取用户在进度条选中的那一帧（默认第 0 秒）。
        hd = exdir / f"v{seq}_{quality}.mp4"
        if "mp4" in formats and hd.exists():           # 本次已渲高清片 → 直接抽帧，免重渲
            r = await render.frame_at(hd, d, cover_time)
        else:                                          # 否则按清晰度临渲一版再抽该帧
            r = await render.cover_at(v["code"], d, quality=quality, t=cover_time)
        if r.ok:
            products.append({"kind": "cover", "label": "封面图", "url": _media_url(d),
                             "filename": _dl_name(pid, seq, "png", "_封面")})

    if products:
        # 导出成片后丢弃临时的解说预览音频（导出已生成自己的配音轨，预览 mp3 不再需要）
        try:
            narr = config.project_dir(pid) / "previews" / f"v{seq}_narr.mp3"
            if narr.exists():
                narr.unlink()
        except OSError:
            pass
        await broker.publish(pid, {"type": "export_ready", "seq": seq, "products": products})
    else:
        await broker.publish(pid, {"type": "export_failed", "seq": seq,
                                   "error": "未选择任何导出格式或全部失败"})


# ── 队列 ────────────────────────────────────────────────────
class JobQueue:
    def __init__(self, workers: int = 1) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.workers = workers
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        for _ in range(self.workers):
            self._tasks.append(asyncio.create_task(self._worker()))

    async def _worker(self) -> None:
        while True:
            pid, coro = await self.q.get()
            try:
                await coro
            except Exception as e:  # noqa: BLE001
                await broker.publish(pid, {"type": "failed", "error": f"内部错误：{e}"})
            finally:
                self.q.task_done()

    async def submit_generation(self, pid: str, prompt: str) -> int:
        pos = self.q.qsize()
        await self.q.put((pid, _run_generation(pid, prompt)))
        return pos

    async def submit_export(self, pid: str, seq: int, formats: list[str],
                            quality: str, vo: dict, cover_time: float = 0.0) -> int:
        pos = self.q.qsize()
        await self.q.put((pid, _run_export(pid, seq, formats, quality, vo, cover_time)))
        return pos


queue = JobQueue(workers=config.RENDER_WORKERS)
