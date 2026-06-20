"""全局配置与路径约定（仿 OpenMentor：SQLite + 文件落盘、数据自主）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── 目录约定 ──────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent.parent          # .../Any2Manim/app
BACKEND_DIR = APP_DIR / "backend"
FRONTEND_DIR = APP_DIR / "frontend"
PROMPTS_DIR = BACKEND_DIR / "prompts"

# data 是指向 data.nosync 的符号链接（防 iCloud 驱逐）
DATA_DIR = APP_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
ASSETS_DIR = DATA_DIR / "assets"
DB_PATH = DATA_DIR / "any2manim.db"
CONFIG_PATH = DATA_DIR / "config.json"     # API 厂商/Key/模型（BYO-Key，本地存）

# ── 渲染可执行文件（同 venv 内的 manim）──────────────────────
VENV_BIN = Path(sys.executable).parent
MANIM_BIN = str(VENV_BIN / "manim")

# ── ffmpeg/ffprobe：优先用 static-ffmpeg（自带 libass，支持字幕烧录）──────
# 系统 homebrew ffmpeg 不带 libass，烧录字幕用不了；static-ffmpeg 是 pip 装的
# 全功能静态二进制，用户侧也随 pip 一起装好。首次解析会下载一次（~30MB）。
_ffmpeg_cache: Optional[tuple[str, str]] = None


def ffmpeg_bins() -> "tuple[str, str]":
    """返回 (ffmpeg, ffprobe) 路径；static-ffmpeg 不可用时回退系统命令。"""
    global _ffmpeg_cache
    if _ffmpeg_cache is None:
        try:
            from static_ffmpeg import run
            fp, fpb = run.get_or_fetch_platform_executables_else_raise()
            _ffmpeg_cache = (fp, fpb)
        except Exception:  # noqa: BLE001
            _ffmpeg_cache = ("ffmpeg", "ffprobe")
    return _ffmpeg_cache

# ── 渲染护栏（防复杂场景/滥用拖垮机器）─────────────────────────
SCENE_CLASS_NAME = "GeneratedScene"        # 钉死类名，便于强约束输出 + 渲染定位
DRYRUN_TIMEOUT = 45                         # 验证渲染超时(s)
PREVIEW_TIMEOUT = 120                       # 低清预览超时(s)
EXPORT_TIMEOUT = 600                        # 高清导出超时(s)
MAX_PREVIEW_SECONDS = 60                    # 单个场景时长上限（防超长）

# ── 自愈循环预算（第七节）────────────────────────────────────
HEAL_MAX_ATTEMPTS = 4                       # 硬预算：最多尝试次数（主约束）
HEAL_MAX_SECONDS = 180                      # 硬预算：heal 循环累计时间上限（含真实模型修复调用）
HEAL_SAME_ERROR_STOP = 2                    # 同错连续 N 次即停

# ── worker 数（个人版=1 串行；校园版按核数）──────────────────
RENDER_WORKERS = int(os.environ.get("A2M_WORKERS", "1"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR, ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid
