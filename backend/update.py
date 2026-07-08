"""在线更新：检查更新源 → 下载校验 → 解压待命 → updater 脚本换目录并重启。

参考 OpenKeyHub v1.1.0 已验证的做法，适配 Any2Manim 的**免安装包**目录：

    Any2Manim-Win/ (PACKAGE_ROOT)         ← updater 脚本在此运行
      启动 Any2Manim.bat                  ← 启动器，永不更新
      python/  tinytex/  ffmpeg           ← 内置运行时，仅大版本换整包，不在线更新
      app/ (APP_DIR)
        backend/ frontend/ CHANGELOG.md … ← 程序本体（代码），在线更新只换白名单这几项
        data/ data.nosync/ media/ *.db    ← 用户项目/视频/数据库/配置，**升级绝不触碰**

刻意**按白名单选择性覆盖**（只动 app/ 下的代码子项），不做整目录 swap——data/media 与
backend/frontend 在 app/ 内平级但不在白名单，天然保留；失败可从 app/_bak/ 手动回滚。

个人版单机、无鉴权（与其余 API 一致），路由不加 Depends。

更新源是一个静态目录 URL，内含 version.json：
    {"version":"1.0.7","zip":"Any2Manim-update-1.0.7.zip","sha256":"…","size":123,"notes":"…"}
增量 zip 顶层 = backend/、frontend/、CHANGELOG.md（可选 README.md/requirements.txt/docs）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from . import config, store

router = APIRouter(prefix="/api/update", tags=["update"])

# 只更新这几项（白名单，都在 app/ 下的代码项）；其余（data/ media/ 数据库/ .env /
# 包根的 python/ tinytex/ 启动器）一律保留。边界照 app/.gitignore。
APP_ENTRIES = ["backend", "frontend", "CHANGELOG.md", "README.md", "requirements.txt", "docs"]

# 下载/解压进度（单机部署，模块级状态足够）
STATE: dict = {"state": "idle", "pct": 0, "msg": "", "info": None}

_VER_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]")


class SourceIn(BaseModel):
    update_url: str = ""


def _stored_update_url() -> str:
    return (store.get_setting("update_url") or "").strip().rstrip("/")


def _visible_update_url() -> str:
    """只把用户自填源回给前端；启动器注入的官方默认源不在页面暴露。"""
    saved = _stored_update_url()
    default = (config.DEFAULT_UPDATE_URL or "").rstrip("/")
    return "" if default and saved == default else saved


def _update_url() -> str:
    return (_stored_update_url() or config.DEFAULT_UPDATE_URL or "").rstrip("/")


def _ver_tuple(v: str):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:  # noqa: BLE001
        return (0,)


def _root() -> "Path | None":
    return config.PACKAGE_ROOT if config.PORTABLE else None


def _new_dir() -> Path:
    """app_new 位置：便携包放包根（与 app/ 平级）；dev 放数据目录（仅演练不应用）。"""
    root = _root()
    return (root / "app_new") if root else (config.DATA_DIR / "updates" / "app_new")


def _read_pkg_version(new_dir: Path) -> str:
    """读增量包里的 CHANGELOG.md 顶部版本号（Any2Manim 版本真值在 CHANGELOG，不在 __init__）。"""
    try:
        for ln in (new_dir / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
            m = _VER_RE.match(ln.strip())
            if m:
                return m.group(1)
    except OSError:
        pass
    return "?"


def _launcher_name() -> str:
    """免安装包启动器文件名（中文 + 空格：『启动 Any2Manim.bat』）；找不到用已知默认。"""
    root = _root()
    if root:
        for p in sorted(root.glob("*.bat")):
            if p.name.startswith("启动") or "Any2Manim" in p.name:
                return p.name
    return "启动 Any2Manim.bat"


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "a2m-updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


@router.get("/status")
def status():
    visible_url = _visible_update_url()
    active_url = _update_url()
    return {"version": config.app_version(), "portable": config.PORTABLE,
            "update_url": visible_url,
            "source_configured": bool(active_url),
            "using_default_update_url": bool(config.DEFAULT_UPDATE_URL and not visible_url),
            "pending": (_new_dir() / "backend" / "main.py").exists(),
            **{k: STATE[k] for k in ("state", "pct", "msg")}}


@router.post("/source")
def save_source(body: SourceIn):
    url = body.update_url.strip().rstrip("/")
    if config.DEFAULT_UPDATE_URL and url == config.DEFAULT_UPDATE_URL.rstrip("/"):
        url = ""
    store.set_setting("update_url", url)
    return {"ok": True, "update_url": _visible_update_url(),
            "using_default_update_url": bool(config.DEFAULT_UPDATE_URL and not _visible_update_url())}


@router.post("/check")
def check():
    base = _update_url()
    if not base:
        return {"ok": False, "msg": "尚未配置更新源地址"}
    try:
        info = _fetch_json(base + "/version.json")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"无法连接更新源：{str(e)[:120]}"}
    latest = str(info.get("version", ""))
    newer = _ver_tuple(latest) > _ver_tuple(config.app_version())
    STATE["info"] = info if newer else None
    return {"ok": True, "newer": newer, "current": config.app_version(),
            "latest": latest, "notes": info.get("notes", ""),
            "size": info.get("size", 0)}


def _download_job(base: str, info: dict):
    try:
        STATE.update(state="downloading", pct=0, msg="正在下载更新包…", info=info)
        zip_name = info["zip"]
        url = zip_name if zip_name.startswith("http") else f"{base}/{zip_name}"
        tmp_dir = config.DATA_DIR / "updates"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_zip = tmp_dir / "pending.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "a2m-updater"})
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp_zip, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    STATE["pct"] = int(done * 90 / total)
        want = str(info.get("sha256", "")).lower()
        if want and h.hexdigest().lower() != want:
            raise RuntimeError("更新包校验失败（sha256 不符），已放弃")
        STATE.update(pct=92, msg="正在解压…")
        new_dir = _new_dir()
        if new_dir.exists():
            shutil.rmtree(new_dir)
        new_dir.mkdir(parents=True)
        with zipfile.ZipFile(tmp_zip) as z:
            for n in z.namelist():          # 防路径穿越
                if n.startswith("/") or ".." in Path(n).parts:
                    raise RuntimeError(f"更新包含非法路径：{n}")
            z.extractall(new_dir)
        # 有些打包工具会把内容套一层同名顶层夹，自动拆一层
        if not (new_dir / "backend" / "main.py").exists():
            subs = [p for p in new_dir.iterdir() if p.is_dir()]
            if len(subs) == 1 and (subs[0] / "backend" / "main.py").exists():
                inner = subs[0]
                for item in inner.iterdir():
                    shutil.move(str(item), str(new_dir / item.name))
                inner.rmdir()
        if not (new_dir / "backend" / "main.py").exists():
            raise RuntimeError("更新包结构不对（缺 backend/main.py）")
        got_ver = _read_pkg_version(new_dir)
        tmp_zip.unlink(missing_ok=True)
        STATE.update(state="ready", pct=100,
                     msg=f"新版 {got_ver} 已就绪，点击「重启完成升级」")
    except Exception as e:  # noqa: BLE001
        STATE.update(state="error", msg=str(e)[:200])


@router.post("/download")
def download():
    if STATE["state"] == "downloading":
        return {"ok": True}
    info = STATE.get("info")
    if not info:
        return {"ok": False, "msg": "请先检查更新"}
    threading.Thread(target=_download_job, args=(_update_url(), info),
                     daemon=True).start()
    return {"ok": True}


@router.get("/progress")
def progress():
    return {k: STATE[k] for k in ("state", "pct", "msg")}


# ---------------- 应用更新：写 updater 脚本 → 退出主程序 ----------------
# updater 在**包根**运行：按白名单把 app\<项> 挪进 app\_bak\，再把 app_new\<项> 挪进 app\，
# 然后重启启动器。保 app\data app\media 数据库 与包根 python/ tinytex/ 启动器。失败可从 app\_bak\ 回滚。

_BAT = """@echo off
cd /d "%~dp0"
timeout /t 2 /nobreak >nul
rd /s /q "app\\_bak" >nul 2>&1
mkdir "app\\_bak" >nul 2>&1
{swaps}
rd /s /q "app_new" >nul 2>&1
start "" "{launcher}"
(goto) 2>nul & del "%~f0"
"""

_BAT_SWAP = (
    'if exist "app\\{name}" move "app\\{name}" "app\\_bak\\{name}" >nul 2>&1\n'
    'move "app_new\\{name}" "app\\{name}" >nul 2>&1'
)

_SH = """#!/bin/sh
cd "$(dirname "$0")"
sleep 2
rm -rf "app/_bak" && mkdir -p "app/_bak"
{swaps}
rm -rf "app_new"
nohup sh "app/start.sh" >/dev/null 2>&1 &
rm -f "$0"
"""

_SH_SWAP = (
    '[ -e "app/{name}" ] && mv "app/{name}" "app/_bak/{name}"\n'
    'mv "app_new/{name}" "app/{name}"'
)


@router.post("/apply")
def apply():
    root = _root()
    if not root:
        return {"ok": False,
                "msg": "开发模式下更新包已下载到数据目录 updates/app_new，不执行目录切换"
                       "（仅免安装包模式支持一键重启升级）"}
    new_dir = _new_dir()
    if not (new_dir / "backend" / "main.py").exists():
        return {"ok": False, "msg": "没有待应用的新版，请先下载"}
    is_win = os.name == "nt"
    entries = [e for e in APP_ENTRIES if (new_dir / e).exists()]
    if is_win:
        swaps = "\n".join(_BAT_SWAP.format(name=e) for e in entries)
        script = root / "updater.bat"
        # bat 必须 GBK + CRLF、且【不要】chcp 65001：cmd 用系统 936 代码页解析，
        # 才能匹配中文启动器名『启动 Any2Manim.bat』；UTF-8/LF 会解码错、拉不起来。
        body = _BAT.format(swaps=swaps, launcher=_launcher_name())
        script.write_bytes(body.replace("\n", "\r\n").encode("gbk"))
        subprocess.Popen(["cmd", "/c", str(script)], cwd=str(root),
                         creationflags=0x00000008 | 0x00000200)  # DETACHED|NEW_GROUP
    else:
        swaps = "\n".join(_SH_SWAP.format(name=e) for e in entries)
        script = root / "updater.sh"
        script.write_text(_SH.format(swaps=swaps), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], cwd=str(root),
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True, "msg": "正在重启升级，约 10 秒后刷新页面"}
