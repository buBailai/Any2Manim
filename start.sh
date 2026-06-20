#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT="${A2M_PORT:-8848}"
# 绑定 0.0.0.0 → 支持局域网访问（同网段其他设备用本机 IP 打开）
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "本机IP")
echo "Any2Manim 启动中…"
echo "  本机：   http://127.0.0.1:${PORT}"
echo "  局域网： http://${IP}:${PORT}"
echo "  (如局域网打不开：检查 macOS 系统设置→网络→防火墙是否拦截 Python)"
echo "准备 ffmpeg（首次会下载一次带 libass 的版本，用于字幕烧录）…"
./.venv/bin/python -c "from backend import config; config.ffmpeg_bins()" 2>/dev/null
exec ./.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}" "$@"
