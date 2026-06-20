"""出厂"好默认"+ 组件库（第八节 B-6/7 命门）。

渲染时作为 preamble 注入到生成代码前 —— 这样即便 LLM 只产出最朴素内容，
也自带统一背景/色板/字体/构图，"效果好"一大半来自这里，不指望 LLM 每次想到。

存储/编辑/展示的是 LLM 干净代码（不含 preamble）；只在跑 manim 时 compose 注入。
"""
from __future__ import annotations

# 与前端画布同款靛蓝深色，观感统一
PREAMBLE = '''from manim import *
import os as _os

# ── Any2Manim 出厂好默认 ───────────────────────────────
config.background_color = "#12141D"


def a2m_asset(name):
    """解析老师上传的素材为可用路径（图片/SVG）。

    渲染时由 A2M_ASSET_DIR 环境变量指向当前项目的素材目录。
    用法：ImageMobject(a2m_asset("分子.png")) / SVGMobject(a2m_asset("地图.svg"))
    """
    return _os.path.join(_os.environ.get("A2M_ASSET_DIR", "."), name)

A2M_ACCENT = "#5B5BD6"   # 靛蓝
A2M_VIO = "#8B5CF6"      # 紫
A2M_CYAN = "#3FB6C6"     # 青
A2M_INK = "#EDEFF6"      # 近白文字


def a2m_title(text, **kw):
    """标题：渐变靛蓝紫，大字号。"""
    kw.setdefault("font_size", 60)
    return Text(text, gradient=(BLUE, PURPLE), **kw)


def a2m_caption(text, **kw):
    """副说明/注释：灰色中字号。"""
    kw.setdefault("font_size", 30)
    kw.setdefault("color", GREY_B)
    return Text(text, **kw)


def a2m_highlight(mobj, color=YELLOW):
    """高亮强调动画。"""
    return Indicate(mobj, color=color, scale_factor=1.15)


def a2m_axes(**kw):
    """统一风格坐标系。"""
    kw.setdefault("axis_config", {"include_tip": True, "stroke_width": 2})
    return Axes(**kw)
# ── 以下为本场景代码 ───────────────────────────────────
'''


def compose(code: str) -> str:
    """把好默认 preamble 注入到生成代码前，供渲染使用。"""
    return PREAMBLE + "\n" + code
