"""全局配置与路径约定（仿 OpenMentor：SQLite + 文件落盘、数据自主）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


# ── LaTeX 可用性（MathTex 公式必须；自适应：本机/内置都能用就用，否则降级 Text）──
_latex_cache: "bool | None" = None


def _bundled_latex_bin() -> "object":
    """免安装包把精简 TeX(TinyTeX) 放在 app 同级的 tinytex/ 下，返回其 bin 目录；没有则 None。

    TeX Live 是相对路径布局、可移植，只要把 bin 目录加进 PATH，latex/dvisvgm 就能自洽找到
    texmf-dist。dev/Mac 上没有该目录 → 直接 None，不影响本机已装的系统 LaTeX。
    """
    root = Path(__file__).resolve().parent.parent.parent       # 免安装包根 = app 的上一级
    for sub in ("tinytex/bin/windows", "tinytex/bin/win32",
                "tinytex/bin/universal-darwin", "tinytex/bin/x86_64-linux"):
        d = root / sub
        if d.is_dir() and (list(d.glob("latex*")) or list(d.glob("xelatex*"))):
            return d
    return None


def _inject_bundled_latex() -> None:
    """启动时把内置 TeX 的 bin 目录前置进 PATH，让渲染子进程的 latex/dvisvgm 可被找到。"""
    d = _bundled_latex_bin()
    if d is not None:
        cur = os.environ.get("PATH", "")
        if str(d) not in cur:
            os.environ["PATH"] = str(d) + os.pathsep + cur


_inject_bundled_latex()


def has_latex() -> bool:
    """本机能否渲染 MathTex 公式（需 latex + dvisvgm）。结果缓存，避免反复探测。"""
    global _latex_cache
    if _latex_cache is None:
        _latex_cache = bool(shutil.which("latex") and shutil.which("dvisvgm"))
    return _latex_cache

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

# ── 渲染调用：用 `python -m manim` 而非 console script ──────────
# 跨平台稳：Windows 内嵌 Python 没有 bin/manim（console script 是 Scripts\manim.exe），
# 直接按路径找 manim 会 WinError 2；用当前解释器 -m manim 免去找 .exe，三端一致。
MANIM_CMD = [sys.executable, "-m", "manim"]

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
DRYRUN_TIMEOUT = 60                         # 验证渲染超时(s)
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


# ── API 配置：按厂商分别长期保存（每个厂商各记各的 Key/地址/模型）──────────
# 磁盘格式：{"active": "<厂商key>", "providers": {"deepseek": {base_url,api_key,model}, ...}}
# 兼容旧的扁平格式 {provider,base_url,api_key,model}：读时自动并入新结构，下次保存即固化。
def read_config() -> dict:
    """读 config.json，统一成 {active, providers:{p:{base_url,api_key,model}}}。"""
    if not CONFIG_PATH.exists():
        return {"active": "", "providers": {}}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"active": "", "providers": {}}
    if isinstance(raw.get("providers"), dict):
        raw.setdefault("active", "")
        return raw
    # 旧扁平格式 → 迁移
    prov = raw.get("provider", "")
    out: dict = {"active": prov, "providers": {}}
    if prov:
        out["providers"][prov] = {"base_url": raw.get("base_url", ""),
                                  "api_key": raw.get("api_key", ""),
                                  "model": raw.get("model", "")}
    return out


def provider_flat(p: str) -> dict:
    """某厂商的扁平配置（喂给 llm.from_config）。"""
    d = read_config().get("providers", {}).get(p, {})
    return {"provider": p, "base_url": d.get("base_url", ""),
            "api_key": d.get("api_key", ""), "model": d.get("model", "")}


def active_flat() -> dict:
    """当前激活厂商的扁平配置（喂给 llm.from_config）。"""
    return provider_flat(read_config().get("active", ""))


def save_provider(provider: str, base_url: str, api_key: str, model: str) -> None:
    """保存某厂商配置并设为激活。api_key 留空时保留该厂商原 Key（改模型/地址不必重填）。"""
    c = read_config()
    provs = c.setdefault("providers", {})
    cur = provs.get(provider, {})
    if not api_key and cur.get("api_key"):
        api_key = cur["api_key"]
    provs[provider] = {"base_url": base_url, "api_key": api_key, "model": model}
    c["active"] = provider
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
