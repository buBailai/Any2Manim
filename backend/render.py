"""Manim 渲染封装（asyncio 子进程，带超时强杀）。

四种调用：
- syntax_check：ast 秒级静态校验（不起进程）
- dry_run     ：--dry_run 只构建场景不出文件，几秒确认"代码能跑"（自愈循环用）
- thumbnail   ：-s 单帧 png，秒出首帧给即时反馈
- preview     ：-q l 480p15 低清短片（快迭代）
- export      ：-q h 高清成片（定稿）
"""
from __future__ import annotations

import ast
import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config, defaults

SC = config.SCENE_CLASS_NAME


@dataclass
class RunResult:
    ok: bool
    output: Optional[Path] = None     # 产物路径（thumbnail/preview/export）
    traceback: str = ""               # 失败时的精简 traceback
    timed_out: bool = False


# ── 代码清洗与静态校验 ──────────────────────────────────────
_FENCE = re.compile(r"^```[a-zA-Z]*\n|\n```$", re.MULTILINE)


def extract_code(raw: str) -> str:
    """去掉可能的 markdown 围栏 / 前后废话，保留纯 Python。"""
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```(?:python|py)?\s*\n(.*?)```", s, re.DOTALL)
        if m:
            s = m.group(1)
    return s.strip() + "\n"


def syntax_check(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"
    if f"class {SC}" not in code:
        return False, f"未找到固定类名 class {SC}(Scene)"
    return True, ""


# 「能教」静态检查（Phase 3）：零 LLM、纯源码规则，发现「能跑但不像教学」的问题。
# 只在代码已通过 dry_run 后跑；block 级会触发一次教学修复，warn 级仅记录。
_GEOMETRY_TOKENS = (
    "Circle", "Square", "Rectangle", "RoundedRectangle", "Polygon", "Triangle",
    "Line", "Arrow", "DoubleArrow", "Vector", "Dot", "Angle", "Brace", "DashedVMobject",
    "Axes", "NumberPlane", "NumberLine", "ParametricFunction", "FunctionGraph",
    "ImageMobject", "SVGMobject", ".plot(", "a2m_axes", "a2m_vt_graph", "a2m_timeline",
    "a2m_number_line", "a2m_compare_layout",
)


def teachability_issues(code: str, user_prompt: str = "") -> list[dict]:
    """返回教学质量问题列表：[{key, severity('block'|'warn'), msg}]。空 = 没发现问题。"""
    issues: list[dict] = []
    has_visual = any(tok in code for tok in _GEOMETRY_TOKENS)
    has_text = ("Text(" in code or "MathTex(" in code or "a2m_title" in code
                or "a2m_headline" in code or "a2m_caption" in code)
    if has_text and not has_visual:
        issues.append({"key": "generic_title_card_only", "severity": "block",
                       "msg": "整片只有文字、没有任何图形/坐标/图像——像 PPT 标题卡，不是教学动画。"
                              "请按教学镜头计划补上真正的视觉对象与动画（图形/坐标系/运动）。"})
    if "a2m_takeaway" not in code and not re.search(r"结论|总结|要点|记住|takeaway", code, re.I):
        issues.append({"key": "no_final_takeaway", "severity": "warn",
                       "msg": "结尾没有明确结论（TAKEAWAY）。"})
    if re.search(r"to_edge\(\s*DOWN\s*\)", code):
        issues.append({"key": "subtitle_zone_occupied", "severity": "warn",
                       "msg": "有元素 to_edge(DOWN) 未留 buff，可能贴到底部字幕安全区。"})
    plays, waits = code.count("self.play"), code.count("self.wait")
    if plays >= 6 and waits == 0:
        issues.append({"key": "too_fast_pacing", "severity": "warn",
                       "msg": "几乎没有停顿（self.wait），节奏可能过快。"})
    return issues


_EXC_RE = re.compile(r"\w*(?:Error|Exception|Warning)\s*:")


def condense_traceback(stderr: str, limit: int = 1400) -> str:
    """抽取报错关键段，避免把整篇 traceback 喂回。

    优先级：真正的异常行（`XxxError: msg`）+ 指向用户代码的 `❱` 行 > 末尾若干行。
    manim 用 rich 渲染 traceback（含框线/内部帧），需挑出真正有用的几行。
    """
    raw = [l.rstrip() for l in stderr.strip().splitlines() if l.strip()]
    # 真正的异常摘要行：rich 输出在框外，无 │ 框线、非 except 帧
    exc = [l.strip() for l in raw
           if _EXC_RE.search(l) and "│" not in l and not l.lstrip().startswith("except")]
    pointer = [l.strip(" │") for l in raw if "❱" in l]          # rich 的报错指针行
    tail = [l for l in raw[-6:] if "│" not in l]
    # 异常行放最后（最显眼），前面给指针与尾部上下文
    ordered = pointer[-2:] + tail + exc[-2:]
    picked = "\n".join(dict.fromkeys(x for x in ordered if x))
    return picked[-limit:] or "\n".join(raw[-4:])


# ── 子进程执行 ──────────────────────────────────────────────
async def _run_manim(args: list[str], timeout: int) -> tuple[int, str, bool]:
    proc = await asyncio.create_subprocess_exec(
        *config.MANIM_CMD, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace"), False
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -9, "", True


def _write_code(code: str, work: Path) -> Path:
    """渲染前注入好默认 preamble（存储/编辑的仍是 code 本身）。"""
    work.mkdir(parents=True, exist_ok=True)
    f = work / "scene.py"
    f.write_text(defaults.compose(code), encoding="utf-8")
    return f


def _find_output(media_dir: Path, ext: str) -> Optional[Path]:
    cands = sorted(media_dir.rglob(f"*.{ext}"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    # 跳过 manim 的 partial_movie_files 中间产物
    for c in cands:
        if "partial_movie_files" not in str(c):
            return c
    return cands[0] if cands else None


async def dry_run(code: str) -> RunResult:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        f = _write_code(code, work)
        rc, out, to = await _run_manim(
            ["render", "--dry_run", "-q", "l", "--media_dir", str(work / "m"),
             str(f), SC],
            config.DRYRUN_TIMEOUT)
        if to:
            return RunResult(False, traceback="渲染超时（场景可能过重）", timed_out=True)
        if rc == 0:
            return RunResult(True)
        return RunResult(False, traceback=condense_traceback(out))


async def _render_to(code: str, dest: Path, *, quality: str, fmt: str,
                     save_frame: bool, timeout: int) -> RunResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        f = _write_code(code, work)
        media = work / "m"
        args = ["render", "-q", quality, "--format", fmt,
                "--media_dir", str(media)]
        if save_frame:
            args.append("-s")
        args += [str(f), SC]
        rc, out, to = await _run_manim(args, timeout)
        if to:
            return RunResult(False, traceback="渲染超时", timed_out=True)
        if rc != 0:
            return RunResult(False, traceback=condense_traceback(out))
        produced = _find_output(media, fmt)
        if not produced:
            return RunResult(False, traceback="渲染完成但未找到产物文件")
        shutil.copy(produced, dest)
        return RunResult(True, output=dest)


async def thumbnail(code: str, dest: Path) -> RunResult:
    return await _render_to(code, dest, quality="l", fmt="png",
                            save_frame=True, timeout=config.DRYRUN_TIMEOUT)


async def preview(code: str, dest: Path) -> RunResult:
    return await _render_to(code, dest, quality="l", fmt="mp4",
                            save_frame=False, timeout=config.PREVIEW_TIMEOUT)


async def export(code: str, dest: Path, *, quality: str = "h") -> RunResult:
    return await _render_to(code, dest, quality=quality, fmt="mp4",
                            save_frame=False, timeout=config.EXPORT_TIMEOUT)


async def _run_ffmpeg(args: list[str], timeout: int) -> bool:
    proc = await asyncio.create_subprocess_exec(
        config.ffmpeg_bins()[0], "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False


async def export_gif(code: str, dest: Path, *, width: int = 480, fps: int = 12) -> RunResult:
    """轻量 gif：先渲低清 mp4 → ffmpeg 调色板两遍法转 gif（小而清晰）。

    manim 原生 --format gif 是逐帧无压缩，几秒动画就几十 MB；这里用 ffmpeg
    palettegen/paletteuse + 缩放 + 降帧，体积通常压到几百 KB ~ 数 MB。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        mp4 = work / "src.mp4"
        r = await _render_to(code, mp4, quality="l", fmt="mp4",
                             save_frame=False, timeout=config.PREVIEW_TIMEOUT)
        if not r.ok:
            return r
        palette = work / "pal.png"
        vf = f"fps={fps},scale={width}:-1:flags=lanczos"
        ok1 = await _run_ffmpeg(["-i", str(mp4), "-vf", f"{vf},palettegen", str(palette)], 60)
        if not ok1:
            return RunResult(False, traceback="gif 调色板生成失败")
        ok2 = await _run_ffmpeg(
            ["-i", str(mp4), "-i", str(palette), "-lavfi", f"{vf}[x];[x][1:v]paletteuse",
             str(dest)], 90)
        if not ok2 or not dest.exists():
            return RunResult(False, traceback="gif 合成失败")
        return RunResult(True, output=dest)


async def cover(code: str, dest: Path, *, quality: str = "h") -> RunResult:
    # 封面图 = 高清末帧
    return await _render_to(code, dest, quality=quality, fmt="png",
                            save_frame=True, timeout=config.PREVIEW_TIMEOUT)


async def frame_at(video: Path, dest: Path, t: float = 0.0) -> RunResult:
    """从已渲染视频里抽取第 t 秒的一帧——精确对应用户在预览里停留/选中的画面。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, float(t or 0.0))
    ok = await _run_ffmpeg(
        ["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-update", "1", str(dest)], 60)
    if not ok or not dest.exists():
        return RunResult(False, traceback="封面抽帧失败")
    return RunResult(True, output=dest)


async def cover_at(code: str, dest: Path, *, quality: str = "h", t: float = 0.0) -> RunResult:
    """没有现成高清视频时：按清晰度临渲一版 mp4，再抽取第 t 秒那帧做封面。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "src.mp4"
        r = await _render_to(code, mp4, quality=quality, fmt="mp4",
                             save_frame=False, timeout=config.EXPORT_TIMEOUT)
        if not r.ok:
            return r
        return await frame_at(mp4, dest, t)
