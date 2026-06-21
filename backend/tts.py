"""配音 + 字幕（第四节）：edge-tts 合成旁白 → 生成 srt → ffmpeg 合成到视频。

仅在高清导出时做（预览静音省时）。中文 WordBoundary 不可靠，字幕按句子+字数比例估时。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

import edge_tts

from . import config


def _log(msg: str) -> None:
    """打到 uvicorn 控制台（黑窗口），便于排查配音/字幕问题。"""
    print(f"[配音] {msg}", file=sys.stderr, flush=True)

# 可选音色（教学场景挑了清晰自然的几个）
VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 女声（自然）"},
    {"id": "zh-CN-YunxiNeural", "label": "云希 · 男声（沉稳）"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 女声（亲切）"},
    {"id": "zh-CN-YunyangNeural", "label": "云扬 · 男声（播音）"},
]
VOICE_IDS = {v["id"] for v in VOICES}
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


async def synth(text: str, out_mp3: Path, *, voice: str = DEFAULT_VOICE,
                rate: str = "+0%") -> bool:
    if voice not in VOICE_IDS:
        voice = DEFAULT_VOICE
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    # edge-tts 连的是微软 speech.platform.bing.com，大陆网络常被拦/限流报 403。
    # edge-tts 默认【不走】系统代理，所以光开 Clash 没用——必须显式把代理传给它。
    # 取代理优先级：A2M_TTS_PROXY 环境变量 > 系统 HTTPS/ALL_PROXY 环境变量 >
    # 操作系统系统代理（Windows 注册表里 Clash 设的那个，靠 getproxies() 读到）。
    proxy = (os.environ.get("A2M_TTS_PROXY") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY")
             or os.environ.get("all_proxy") or None)
    if not proxy:
        try:
            import urllib.request
            sysp = urllib.request.getproxies()      # Windows 下读注册表里的系统代理
            proxy = sysp.get("https") or sysp.get("http") or None
            if proxy and "://" not in proxy:
                proxy = "http://" + proxy
        except Exception:  # noqa: BLE001
            proxy = None
    _log(f"edge-tts 代理: {proxy or '直连（无代理）'}")
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, proxy=proxy)
        await comm.save(str(out_mp3))
        ok = out_mp3.exists() and out_mp3.stat().st_size > 0
        if not ok:
            _log("edge-tts 返回空音频（可能被网络拦截或服务暂不可用）")
        return ok
    except Exception as e:  # noqa: BLE001
        via = f"，已用代理 {proxy}" if proxy else "，未配代理（大陆网络建议设 A2M_TTS_PROXY 走代理）"
        _log(f"edge-tts 合成失败: {type(e).__name__}: {e}{via}")
        return False


async def _duration(path: Path) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            config.ffmpeg_bins()[1], "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        _log("找不到 ffprobe（static-ffmpeg 未就绪）")
        return 0.0
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


LEAD_IN = 0.35    # 字幕延后出现（约前 5 帧无字幕），避免第一帧/封面就带字幕
WRAP_WIDTH = 18   # 单条字幕字数上限；超出不折成两行，而是切成下一条（保证每画面只 1 行）
SUB_FONTSIZE = 14 # 烧录字幕字号（偏小，少占画面）


def _split_sentences(text: str) -> list[str]:
    # 用户换行视为切句依据；再按句末标点切
    segs: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for p in re.split(r"(?<=[。！？!?；;])", line):
            if p.strip():
                segs.append(p.strip())
    return segs


# 字幕断句标点（在这些符号处切，且不显示标点本身）
_SUB_PUNCT = "，。！？；、：,.!?;:…"
_SUB_SPLIT_RE = re.compile(rf"[{re.escape(_SUB_PUNCT)}]+|\n+")
_HARD_CAP = 26   # 极少数无标点超长句的兜底硬断，防溢出两行


def _segments(text: str, width: int = WRAP_WIDTH) -> list[str]:
    """按标点把旁白切成短句，去掉标点，每个短句 = 一条单行字幕。

    只在标点处断（不会把词断到下一画面）；标点不显示；保证每画面一行。
    破折号 —— 不切（视为连接，保持短句完整）。
    """
    out: list[str] = []
    for raw in _SUB_SPLIT_RE.split(text):
        s = raw.strip()
        if not s:
            continue
        while len(s) > _HARD_CAP:        # 兜底：罕见的无标点超长句才硬断
            out.append(s[:width].rstrip())
            s = s[width:].lstrip()
        if s:
            out.append(s)
    return out


def _ts(sec: float) -> str:
    h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def build_srt(text: str, audio_dur: float, out_srt: Path) -> bool:
    """按句子+字数比例分配时间，生成 srt（中文无可靠词级时间戳的折中）。"""
    segs = _segments(text)
    if not segs or audio_dur <= 0:
        return False
    lead = min(LEAD_IN, audio_dur * 0.1)        # 短视频不让 lead 占太多
    span = max(audio_dur - lead, 0.1)
    total_chars = sum(len(s) for s in segs) or 1
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    lines, t = [], lead
    for i, s in enumerate(segs, 1):             # 每段单独一条字幕、单行
        dur = span * len(s) / total_chars
        start, end = t, min(t + dur, audio_dur)
        t = end
        lines.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{s}\n")
    out_srt.write_text("\n".join(lines), encoding="utf-8")
    return True


async def _run_ffmpeg(args: list[str], timeout: int = 180) -> bool:
    ff = config.ffmpeg_bins()[0]
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        _log(f"找不到 ffmpeg 可执行文件（{ff}）—— static-ffmpeg 可能没下载成功，请联网后重导一次")
        return False
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            _log("ffmpeg 失败: " + " | ".join(tail))
        return proc.returncode == 0
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        _log("ffmpeg 超时")
        return False


async def mux_audio(video: Path, audio: Path, out: Path) -> bool:
    """把旁白音轨合到视频；音频更长则用末帧补齐视频（可靠，无需 libass）。"""
    vdur = await _duration(video)
    adur = await _duration(audio)
    pad = max(0.0, adur - vdur)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(video), "-i", str(audio)]
    if pad > 0.05:
        args += ["-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.2f}[v]",
                 "-map", "[v]", "-map", "1:a"]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    return await _run_ffmpeg(args)


_has_subtitles: Optional[bool] = None


async def subtitles_supported() -> bool:
    """本机 ffmpeg 是否带 subtitles 滤镜（libass）。无则烧录字幕不可用。"""
    global _has_subtitles
    if _has_subtitles is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                config.ffmpeg_bins()[0], "-hide_banner", "-filters",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            _log("找不到 ffmpeg，无法检测字幕滤镜")
            _has_subtitles = False
            return _has_subtitles
        out, _ = await proc.communicate()
        _has_subtitles = b" subtitles " in (out or b"")
        if not _has_subtitles:
            _log("当前 ffmpeg 不含 subtitles(libass) 滤镜 → 烧录字幕不可用，将退化为外挂 SRT")
    return _has_subtitles


async def burn_subtitles(video: Path, srt: Path, out: Path) -> bool:
    """best-effort 烧录字幕；缺 libass 时返回 False（调用方退化为 sidecar）。"""
    if not await subtitles_supported() or not srt.exists():
        return False
    # 在 srt 所在目录执行并用 basename，规避路径含空格/中文/冒号的转义难题
    proc = await asyncio.create_subprocess_exec(
        config.ffmpeg_bins()[0], "-y", "-i", str(video.resolve()),
        "-vf", f"subtitles={srt.name}:force_style='FontSize={SUB_FONTSIZE}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out.resolve()),
        cwd=str(srt.parent), stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.communicate(), timeout=180)
        return proc.returncode == 0 and out.exists()
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        return False
