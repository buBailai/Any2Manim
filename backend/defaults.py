"""出厂"好默认"+ 组件库（第八节 B-6/7 命门）。

渲染时作为 preamble 注入到生成代码前 —— 这样即便 LLM 只产出最朴素内容，
也自带统一背景/色板/字体/构图，"效果好"一大半来自这里，不指望 LLM 每次想到。

存储/编辑/展示的是 LLM 干净代码（不含 preamble）；只在跑 manim 时 compose 注入。
"""
from __future__ import annotations

# 与前端画布同款靛蓝深色，观感统一
# 注：本字符串用 r'''（原始串），因内含 \\usepackage / 行尾续行反斜杠，普通串会被当转义。
PREAMBLE = r'''from manim import *
import os as _os
import numpy as np

# ── Any2Manim 出厂好默认 ───────────────────────────────
config.background_color = "#12141D"

# 精简 + 自适应 TeX 模板：核心宏包(amsmath/amssymb/xcolor)必加，让免安装包内置的轻量
# TinyTeX 也能渲染公式（manim 默认模板还要 physics/calligra/wasysym 等重型宏包，轻量
# 发行版没有会直接失败）。其余增强宏包用 \IfFileExists 探测——装了才加载、没装就跳过，
# 所以老师跑「安装LaTeX.bat」补装后这些会自动生效，没装也绝不报错。
try:
    config.tex_template = TexTemplate(preamble=(
        r"\usepackage{amsmath}" "\n"
        r"\usepackage{amssymb}" "\n"
        r"\usepackage{xcolor}" "\n"
        r"\IfFileExists{mathtools.sty}{\usepackage{mathtools}}{}" "\n"
        r"\IfFileExists{mathrsfs.sty}{\usepackage{mathrsfs}}{}" "\n"
        r"\IfFileExists{physics.sty}{\usepackage{physics}}{}" "\n"
        r"\IfFileExists{siunitx.sty}{\usepackage{siunitx}}{}" "\n"
        r"\IfFileExists{cancel.sty}{\usepackage{cancel}}{}" "\n"
        r"\IfFileExists{esint.sty}{\usepackage{esint}}{}" "\n"))
except Exception:
    pass


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


def a2m_fit(mobj, max_w=12.5, max_h=6.0):
    """超宽/超高时等比缩小到画面安全区内（只缩不放大）。返回对象本身，可链式用。"""
    try:
        if mobj.width > max_w:
            mobj.scale_to_fit_width(max_w)
        if mobj.height > max_h:
            mobj.scale_to_fit_height(max_h)
    except Exception:
        pass
    return mobj


def a2m_clear(scene, *keep, run_time=0.5):
    """镜头清场：淡出当前画面全部元素（keep 里的除外）。新 shot 开头调用，
    避免上一镜元素残留叠加。用法：a2m_clear(self) 或 a2m_clear(self, ax, title)。"""
    doomed = [m for m in list(scene.mobjects) if not any(m is k for k in keep)]
    if doomed:
        scene.play(*[FadeOut(m) for m in doomed], run_time=run_time)
        for m in doomed:
            scene.remove(m)


# ── 教学积木（镜头动作 → 现成组件，省得每次从零造布局）──────────
def a2m_headline(text, **kw):
    """HEADLINE：大标题，自动置顶，超宽自动缩小。返回 Text。"""
    return a2m_fit(a2m_title(text, **kw), max_w=12.8).to_edge(UP, buff=0.5)


def a2m_safe_caption(text, **kw):
    """CAPTION：底部一句解说，自动落在字幕安全区上方（不占 y<-3.0），超宽自动缩小。"""
    kw.setdefault("font_size", 28)
    kw.setdefault("color", GREY_B)
    return a2m_fit(Text(text, **kw), max_w=13.0).to_edge(DOWN, buff=1.3)


def a2m_takeaway(text, **kw):
    """TAKEAWAY：结论卡，醒目金黄 + 圆角框，居中偏下。返回 VGroup。"""
    kw.setdefault("font_size", 34)
    t = a2m_fit(Text(text, color="#FFD479", **kw), max_w=11.5)
    box = SurroundingRectangle(t, color="#FFD479", buff=0.35, corner_radius=0.18)
    box.set_fill("#2A2616", opacity=0.6)
    return VGroup(box, t).move_to([0, -1.0, 0])


def a2m_formula_with_caption(latex, caption, formula_size=52, caption_size=28):
    """REVEAL_FORMULA：公式（裸 LaTeX，勿含中文）+ 下方一句大白话，整体居中，超宽自动缩小。返回 VGroup。"""
    f = a2m_fit(MathTex(latex, font_size=formula_size), max_w=12.0)
    c = a2m_fit(Text(caption, font_size=caption_size, color=GREY_B), max_w=12.5)
    return VGroup(f, c).arrange(DOWN, buff=0.35)


def a2m_compare_layout(left, right, left_label="", right_label="", buff=2.0):
    """COMPARE：两个对象左右分置，各带小标题；整体超出安全区自动缩小。返回 VGroup。"""
    lg = VGroup(Text(left_label, font_size=28, color=A2M_CYAN), left).arrange(DOWN, buff=0.3) \
        if left_label else VGroup(left)
    rg = VGroup(Text(right_label, font_size=28, color=A2M_VIO), right).arrange(DOWN, buff=0.3) \
        if right_label else VGroup(right)
    return a2m_fit(VGroup(lg, rg).arrange(RIGHT, buff=buff), max_w=13.0, max_h=5.5)


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
# ── 布局哨兵（仅验证渲染时启用）：每次 play 后检查越界/文字重叠/堆积，写 JSON 报告 ──
_A2M_REPORT = _os.environ.get("A2M_LAYOUT_REPORT", "")
if _A2M_REPORT:
    import json as _json

    _a2m_viol, _a2m_seen, _a2m_play_n = [], set(), [0]

    def _a2m_label(m):
        t = getattr(m, "original_text", None) or getattr(m, "text", None) \
            or getattr(m, "tex_string", None) or type(m).__name__
        t = str(t).strip().replace("\n", " ")
        return t[:22] + "…" if len(t) > 22 else t

    def _a2m_texts(ms, out):
        for m in ms:
            if isinstance(m, (Text, MarkupText)) or type(m).__name__ in ("MathTex", "Tex", "SingleStringMathTex"):
                if getattr(m, "width", 0) > 0.01:
                    out.append(m)
            else:
                _a2m_texts(getattr(m, "submobjects", []), out)
        return out

    def _a2m_box(m):
        return (m.get_left()[0], m.get_right()[0], m.get_bottom()[1], m.get_top()[1])

    def _a2m_overlap(a, b):
        try:
            al, ar, ab, at = _a2m_box(a)
            bl, br, bb, bt = _a2m_box(b)
        except Exception:
            return 0.0
        w, h = min(ar, br) - max(al, bl), min(at, bt) - max(ab, bb)
        if w <= 0 or h <= 0:
            return 0.0
        amin = min((ar - al) * (at - ab), (br - bl) * (bt - bb))
        return (w * h) / amin if amin > 1e-6 else 0.0

    def _a2m_add(msg):
        key = msg.split("：", 1)[-1]   # 去掉「第N次play后」前缀去重：同一问题只报首次出现
        if key in _a2m_seen or len(_a2m_viol) >= 12:
            return
        _a2m_seen.add(key)
        _a2m_viol.append(msg)
        try:
            with open(_A2M_REPORT, "w", encoding="utf-8") as fh:
                _json.dump(_a2m_viol, fh, ensure_ascii=False)
        except Exception:
            pass

    def _a2m_check(scene):
        n = _a2m_play_n[0]
        for m in scene.mobjects:
            try:
                if m.width < 0.01 and m.height < 0.01:
                    continue
                l, r, b, t = _a2m_box(m)
            except Exception:
                continue
            if r > 7.3 or l < -7.3 or t > 4.15 or b < -4.15:
                _a2m_add(f"第{n}次play后：「{_a2m_label(m)}」超出画面（x∈[{l:.1f},{r:.1f}] y∈[{b:.1f},{t:.1f}]，"
                         f"画面范围 x±7.1/y±4.0）——缩小(a2m_fit/.scale)或移回画面内")
        texts = _a2m_texts(scene.mobjects, [])
        for m in texts:
            try:
                if m.get_bottom()[1] < -3.25:
                    _a2m_add(f"第{n}次play后：文字「{_a2m_label(m)}」压进底部字幕安全区(y<-3.0)——"
                             f"上移或改用 a2m_safe_caption")
            except Exception:
                pass
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                ratio = _a2m_overlap(texts[i], texts[j])
                if ratio > 0.3:
                    _a2m_add(f"第{n}次play后：文字「{_a2m_label(texts[i])}」与「{_a2m_label(texts[j])}」"
                             f"重叠约{int(ratio * 100)}%——错开位置或先 FadeOut 旧文字")
        if len(scene.mobjects) > 14:
            _a2m_add(f"第{n}次play后：同屏 {len(scene.mobjects)} 个顶层元素、过度拥挤——"
                     f"多半是上一镜没清场，镜头切换处调用 a2m_clear(self, 需保留的对象...)")

    _a2m_orig_play = Scene.play

    def _a2m_play(self, *args, **kwargs):
        r = _a2m_orig_play(self, *args, **kwargs)
        _a2m_play_n[0] += 1
        try:
            _a2m_check(self)
        except Exception:
            pass
        return r

    Scene.play = _a2m_play
# ── 以下为本场景代码 ───────────────────────────────────
'''


def compose(code: str) -> str:
    """把好默认 preamble 注入到生成代码前，供渲染使用。"""
    return PREAMBLE + "\n" + code
