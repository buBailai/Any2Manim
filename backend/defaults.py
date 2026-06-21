"""出厂"好默认"+ 组件库（第八节 B-6/7 命门）。

渲染时作为 preamble 注入到生成代码前 —— 这样即便 LLM 只产出最朴素内容，
也自带统一背景/色板/字体/构图，"效果好"一大半来自这里，不指望 LLM 每次想到。

存储/编辑/展示的是 LLM 干净代码（不含 preamble）；只在跑 manim 时 compose 注入。
"""
from __future__ import annotations

# 与前端画布同款靛蓝深色，观感统一
PREAMBLE = '''from manim import *
import os as _os
import numpy as np

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


# ── 教学积木（镜头动作 → 现成组件，省得每次从零造布局）──────────
def a2m_headline(text, **kw):
    """HEADLINE：大标题，自动置顶。返回 Text。"""
    return a2m_title(text, **kw).to_edge(UP, buff=0.5)


def a2m_safe_caption(text, **kw):
    """CAPTION：底部一句解说，自动落在字幕安全区上方（不占 y<-3.0）。"""
    kw.setdefault("font_size", 28)
    kw.setdefault("color", GREY_B)
    return Text(text, **kw).to_edge(DOWN, buff=1.3)


def a2m_takeaway(text, **kw):
    """TAKEAWAY：结论卡，醒目金黄 + 圆角框，居中偏下。返回 VGroup。"""
    kw.setdefault("font_size", 34)
    t = Text(text, color="#FFD479", **kw)
    box = SurroundingRectangle(t, color="#FFD479", buff=0.35, corner_radius=0.18)
    box.set_fill("#2A2616", opacity=0.6)
    return VGroup(box, t).move_to([0, -1.0, 0])


def a2m_formula_with_caption(latex, caption, formula_size=52, caption_size=28):
    """REVEAL_FORMULA：公式（裸 LaTeX，勿含中文）+ 下方一句大白话，整体居中。返回 VGroup。"""
    f = MathTex(latex, font_size=formula_size)
    c = Text(caption, font_size=caption_size, color=GREY_B)
    return VGroup(f, c).arrange(DOWN, buff=0.35)


def a2m_compare_layout(left, right, left_label="", right_label="", buff=2.0):
    """COMPARE：两个对象左右分置，各带小标题。返回 VGroup。"""
    lg = VGroup(Text(left_label, font_size=28, color=A2M_CYAN), left).arrange(DOWN, buff=0.3) \
        if left_label else VGroup(left)
    rg = VGroup(Text(right_label, font_size=28, color=A2M_VIO), right).arrange(DOWN, buff=0.3) \
        if right_label else VGroup(right)
    return VGroup(lg, rg).arrange(RIGHT, buff=buff)


def a2m_term_tour(formula, notes):
    """TERM_TOUR：给 MathTex 各部分加底部小注（逐个 FadeIn 用）。
    notes=[(part_index, "说明"), ...]；越界项静默跳过。返回 VGroup（注释已定位）。"""
    g = VGroup()
    for idx, note in notes:
        try:
            part = formula[idx]
        except Exception:  # noqa: BLE001
            continue
        g.add(Text(str(note), font_size=22, color=YELLOW).next_to(part, DOWN, buff=0.4))
    return g


def a2m_vt_graph(t_max=4, v_max=12, **kw):
    """PLOT：物理常用 v-t（时间-速度）坐标系预设。返回 Axes。"""
    kw.setdefault("x_range", [0, t_max, 1])
    kw.setdefault("y_range", [0, v_max, max(1, int(v_max // 4))])
    kw.setdefault("x_length", 5.5)
    kw.setdefault("y_length", 4.5)
    return a2m_axes(**kw)


def a2m_number_line(x_min=-5, x_max=5, step=1, **kw):
    """数轴预设。返回 NumberLine。"""
    kw.setdefault("x_range", [x_min, x_max, step])
    kw.setdefault("length", 10)
    kw.setdefault("include_numbers", True)
    return NumberLine(**kw)


def a2m_timeline(labels, color=GREY_B, width=11.0):
    """TIMELINE：横向时间轴（历史等）。一条带箭头的轴 + 均匀分布的标签点。返回 VGroup。"""
    axis = Arrow([-width / 2, 0, 0], [width / 2, 0, 0], color=color, stroke_width=3, buff=0)
    g = VGroup(axis)
    xs = np.linspace(-width / 2 + 0.6, width / 2 - 0.6, max(1, len(labels)))
    for x, lab in zip(xs, labels):
        dot = Dot([float(x), 0, 0], radius=0.06, color=color)
        g.add(dot, Text(str(lab), font_size=26).next_to(dot, UP, buff=0.25))
    return g
# ── 以下为本场景代码 ───────────────────────────────────
'''


def compose(code: str) -> str:
    """把好默认 preamble 注入到生成代码前，供渲染使用。"""
    return PREAMBLE + "\n" + code
