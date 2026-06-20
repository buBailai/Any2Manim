"""SQLite 元数据层（仿 OpenMentor openmentor.db）。

核心原则：代码廉价、视频昂贵 —— 长期存「代码 + 元数据 + 缩略图」，
视频分预览(临时)与高清(保留)，版本回溯靠代码重渲。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    subject     TEXT,                      -- 学科标签
    owner       TEXT DEFAULT 'local',      -- 个人版恒为 local；校园版=用户名
    current_version INTEGER,               -- 指向当前 versions.seq
    archived    INTEGER NOT NULL DEFAULT 0,-- 0=活跃 1=已归档（只读）
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS versions (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,          -- 项目内自增版本号 v1,v2...
    prompt      TEXT,                      -- 触发这一版的用户指令
    storyboard  TEXT,                      -- 分镜/计划文本
    code        TEXT,                      -- Manim 代码快照
    status      TEXT NOT NULL,             -- pending|ok|failed
    thumb_path  TEXT,                      -- 缩略图(相对 data/)
    preview_path TEXT,                     -- 低清预览(临时,可被 GC)
    heal_attempts INTEGER DEFAULT 0,
    error       TEXT,                      -- 失败时的大白话原因
    created_at  REAL NOT NULL,
    UNIQUE(project_id, seq)
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    role        TEXT NOT NULL,             -- user|ai
    content     TEXT NOT NULL,
    version_seq INTEGER,                   -- AI 消息关联的版本
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    version_seq INTEGER,
    path        TEXT NOT NULL,             -- 高清 mp4(用户保留)
    cover_path  TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    owner       TEXT DEFAULT 'local',
    project_id  TEXT,                      -- 素材归属项目（生成时按项目注入）
    kind        TEXT,                      -- image|svg|audio
    path        TEXT NOT NULL,
    orig_name   TEXT,                      -- 老师引用素材用的名字
    created_at  REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：为已存在的旧库补列（CREATE TABLE IF NOT EXISTS 不会改已存表）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "archived" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(assets)").fetchall()}
    if "project_id" not in acols:
        conn.execute("ALTER TABLE assets ADD COLUMN project_id TEXT")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(versions)").fetchall()}
    if "narration" not in vcols:
        conn.execute("ALTER TABLE versions ADD COLUMN narration TEXT")


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def now() -> float:
    return time.time()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None
