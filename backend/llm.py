"""LLM 抽象层（BYO-Key，OpenAI 兼容）。

- OpenAILLM：真实厂商/自定义端点（DeepSeek、火山方舟、OpenAI 兼容…）。
- MockLLM：无 Key 时的「演示模式」，返回本环境亲测可渲的 ManimCE 0.20.1 代码，
  让整条管线在没接 Key 时也能端到端跑出视频（机制验证 + 离线 demo）。

task 取值：storyboard | codegen | fix | edit —— 真实模型忽略它，Mock 据它造输出。
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from . import config

SC = config.SCENE_CLASS_NAME


class LLMError(Exception):
    pass


class BaseLLM:
    name = "base"
    demo = False

    def complete(self, system: str, user: str, *, task: str = "codegen",
                 temperature: float = 0.2) -> str:
        raise NotImplementedError


# ── 真实端点 ────────────────────────────────────────────────
class OpenAILLM(BaseLLM):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "no-key"   # Ollama 等本地端点无需真实 Key
        self.model = model
        self.name = model

    def _endpoint(self) -> str:
        # 容错：用户既可填到 /v1 也可直接粘完整 /chat/completions
        b = self.base_url
        if b.endswith("/chat/completions"):
            return b
        return f"{b}/chat/completions"

    def complete(self, system: str, user: str, *, task: str = "codegen",
                 temperature: float = 0.2) -> str:
        url = self._endpoint()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=120) as cli:
                r = cli.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM 接口返回 {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"LLM 调用失败：{e}")


# ── 演示模式（无 Key）──────────────────────────────────────
_PYTHAGORAS = f'''from manim import *


class {SC}(Scene):
    def construct(self):
        # --- 第1步：直角三角形 ---
        a, b = 3.2, 2.4
        A = LEFT * a / 2 + DOWN * b / 2
        B = RIGHT * a / 2 + DOWN * b / 2
        C = RIGHT * a / 2 + UP * b / 2
        tri = Polygon(A, B, C, color={{COLOR}}, fill_opacity=0.25)
        ra = RightAngle(Line(C, B), Line(B, A), length=0.3, color=GREY_B)
        self.play(Create(tri), run_time=1.2 * {{RATE}})
        self.play(FadeIn(ra))
        # --- 第2步：三边标注 ---
        la = MathTex("a").next_to(Line(A, B), DOWN, buff=0.2)
        lb = MathTex("b").next_to(Line(B, C), RIGHT, buff=0.2)
        lc = MathTex("c").next_to(Line(A, C).get_center(), UL, buff=0.2)
        self.play(Write(VGroup(la, lb, lc)), run_time=1.0 * {{RATE}})
        self.wait(0.3)
        # --- 第3步：定理公式 ---
        formula = MathTex("a^2", "+", "b^2", "=", "c^2", font_size=72).to_edge(DOWN, buff=0.6)
        formula[0].set_color({{COLOR}})
        self.play(Write(formula), run_time=1.4 * {{RATE}})
        self.wait(0.6)
'''

_FUNCTION = f'''from manim import *


class {SC}(Scene):
    def construct(self):
        # --- 第1步：坐标系 ---
        ax = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], x_length=7, y_length=4.5,
                  axis_config={{{{"include_tip": True}}}})
        self.play(Create(ax), run_time=1.2 * {{RATE}})
        # --- 第2步：抛物线 ---
        graph = ax.plot(lambda x: x**2, x_range=[-2.2, 2.2], color={{COLOR}})
        label = MathTex("y = x^2", font_size=48).next_to(graph, UR, buff=0.1).set_color({{COLOR}})
        self.play(Create(graph), run_time=1.4 * {{RATE}})
        self.play(Write(label), run_time=0.8 * {{RATE}})
        self.wait(0.6)
'''

_CIRCLE = f'''from manim import *


class {SC}(Scene):
    def construct(self):
        # --- 第1步：圆 ---
        circ = Circle(radius=2, color={{COLOR}}, fill_opacity=0.2)
        self.play(Create(circ), run_time=1.2 * {{RATE}})
        # --- 第2步：半径与公式 ---
        r = Line(circ.get_center(), circ.point_at_angle(PI / 4), color=WHITE)
        rlabel = MathTex("r").next_to(r, UP, buff=0.1)
        self.play(Create(r), Write(rlabel), run_time=1.0 * {{RATE}})
        area = MathTex(r"S = \\pi r^2", font_size=64).to_edge(DOWN, buff=0.6).set_color({{COLOR}})
        self.play(Write(area), run_time=1.2 * {{RATE}})
        self.wait(0.6)
'''

_DEFAULT = f'''from manim import *


class {SC}(Scene):
    def construct(self):
        # --- 第1步：标题 ---
        title = Text("{{TITLE}}", font_size=60, gradient=(BLUE, PURPLE))
        self.play(Write(title), run_time=1.2 * {{RATE}})
        # --- 第2步：副说明 ---
        sub = Text("Any2Manim · 演示场景", font_size=30, color=GREY_B).next_to(title, DOWN, buff=0.5)
        self.play(FadeIn(sub, shift=UP), run_time=0.8 * {{RATE}})
        self.wait(0.6)
'''

# 永远能渲的兜底场景（自愈失败/fix 时回到这里，保住"最后一个能渲的版本"）
_FALLBACK = f'''from manim import *


class {SC}(Scene):
    def construct(self):
        title = Text("{{TITLE}}", font_size=54, gradient=(BLUE, PURPLE))
        self.play(Write(title), run_time=1.0)
        self.wait(0.5)
'''

_COLOR_MAP = {
    "蓝": "BLUE", "blue": "BLUE", "红": "RED", "red": "RED",
    "绿": "GREEN", "green": "GREEN", "紫": "PURPLE", "purple": "PURPLE",
    "黄": "YELLOW", "yellow": "YELLOW", "橙": "ORANGE", "青": "TEAL",
}


def _pick_color(text: str, default: Optional[str] = "BLUE") -> Optional[str]:
    for k, v in _COLOR_MAP.items():
        if k in text.lower():
            return v
    return default


_KNOWN_COLORS = ["BLUE", "RED", "GREEN", "PURPLE", "YELLOW", "ORANGE", "TEAL", "PINK"]


def _mock_edit_blocks(current: str, instruction: str) -> str:
    """演示模式的定向编辑：据指令产出 search/replace 块（颜色 / 节奏）。"""
    blocks = []
    want = _pick_color(instruction, default=None)
    if want:
        for c in _KNOWN_COLORS:
            if c in current and c != want:
                blocks.append((c, want))
                break
    rate_kw = any(w in instruction for w in ("放慢", "慢", "slower"))
    fast_kw = any(w in instruction for w in ("加快", "快", "faster"))
    if (rate_kw or fast_kw) and "* 1.00" in current:
        blocks.append(("* 1.00", "* 1.50" if rate_kw else "* 0.60"))
    out = []
    for s, r in blocks:
        out.append(f"<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE")
    return "\n".join(out)


def _pick_rate(text: str) -> float:
    if any(w in text for w in ("放慢", "慢一", "慢点", "slower", "慢")):
        return 1.5
    if any(w in text for w in ("加快", "快一", "快点", "faster", "快")):
        return 0.6
    return 1.0


def _render_template(tmpl: str, color: str, rate: float, title: str) -> str:
    return (tmpl.replace("{COLOR}", color)
                .replace("{RATE}", f"{rate:.2f}")
                .replace("{TITLE}", title))


class MockLLM(BaseLLM):
    name = "演示模式"
    demo = True

    def complete(self, system: str, user: str, *, task: str = "codegen",
                 temperature: float = 0.2) -> str:
        text = user
        color = _pick_color(text)
        rate = _pick_rate(text)
        title = (re.split(r"[。.\n]", text.strip())[0] or "教学动画")[:18]

        if task == "edit":
            # user = "当前代码：\n<code>\n\n修改要求：<instruction>"
            parts = text.split("修改要求：", 1)
            current = parts[0].replace("当前代码：", "", 1).strip()
            instruction = parts[1].strip() if len(parts) > 1 else text
            return _mock_edit_blocks(current, instruction)

        if task == "narrate":
            return f"让我们一起来看「{title}」。注意观察画面中各部分的变化，理解它们之间的关系。"

        if task == "storyboard":
            return (f"分镜计划（演示）：\n"
                    f"1. 引入：呈现「{title}」的核心对象。\n"
                    f"2. 展开：逐步画出关键元素并标注。\n"
                    f"3. 收尾：给出结论/公式，停留强调。")

        if task == "fix":
            # 演示模式下"修复"= 退回永远能渲的兜底场景（模拟自愈成功收场）
            return _render_template(_FALLBACK, color, rate, title)

        # codegen / edit：按关键词选模板
        low = text.lower()
        if any(k in text for k in ("勾股", "直角三角", "pythag")) or "a²+b²" in text:
            tmpl = _PYTHAGORAS
        elif any(k in text for k in ("函数", "抛物", "图像", "曲线", "graph", "parabola", "x^2", "x²")):
            tmpl = _FUNCTION
        elif any(k in text for k in ("圆", "circle", "面积")):
            tmpl = _CIRCLE
        else:
            tmpl = _DEFAULT
        return _render_template(tmpl, color, rate, title)


def from_config(cfg: Optional[dict]) -> BaseLLM:
    """据 data/config.json 构造 LLM；缺关键项 → 演示模式。

    Ollama 等本地厂商（needs_key=False）无需 Key，只要有 base_url + model 即视为已配置。
    """
    if not cfg or not cfg.get("base_url") or not cfg.get("model"):
        return MockLLM()
    from . import providers
    preset = providers.get(cfg.get("provider", "")) or {}
    needs_key = preset.get("needs_key", True)
    if needs_key and not cfg.get("api_key"):
        return MockLLM()
    return OpenAILLM(cfg["base_url"], cfg.get("api_key", ""), cfg["model"])
