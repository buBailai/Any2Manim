# ManimCE 本环境 API 速查 / 避坑清单（v0）

> 本环境实测：**ManimCE 0.20.1 · Python 3.14**。本清单注入 codegen system prompt，是「第一发少错」头号杠杆。
> 凡与下表冲突的写法一律视为错误。下表的"存在/不存在"均经 `dir(manim)` 实测。

## 0. 铁律
- 引擎是 **ManimCE（社区版）**，**不是 ManimGL（3b1b 版）**。严禁使用 ManimGL/旧版 API。
- `from manim import *` 一行导入；**单文件**；类名固定（由系统指定，如 `GeneratedScene`）；继承 `Scene`。
- 动画在 `construct(self)` 内；用 `self.play(...)` / `self.wait(t)` / `self.add(...)`。
- **只输出可运行 Python 代码**，无 markdown 围栏、无解释文字。

## 1. 高频禁用 → 正确写法（最大错源）
| ❌ 旧/GL 写法（本环境不存在） | ✅ ManimCE 0.20 正确写法 |
|---|---|
| `ShowCreation(m)` | `Create(m)` |
| `ShowCreationThenFadeOut(m)` | `Create` 后再 `FadeOut`，或 `ShowPassingFlash` |
| `from manimlib import *` | `from manim import *` |
| `TexMobject` / `TextMobject` | `MathTex`（公式）/ `Tex`（含文字的 LaTeX）/ `Text`（纯文本） |
| `GraphScene` | `Axes` + `Scene` |
| `CONFIG = {...}` 类变量配置 | 直接在 `construct` 里写，或用 `__init__` 参数 |
| `self.get_graph(f)` | `ax.plot(f)`（`ax = Axes(...)`） |
| `ApplyMethod(m.shift, ...)` | `m.animate.shift(...)` |
| `m.set_color()` 当动画用 | `self.play(m.animate.set_color(BLUE))` |
| `m.set_style(dash_length=...)` | `DashedVMobject(m, num_dashes=15)`（`set_style` 不接受 `dash_length`/`dash_spacing`） |
| `DashedLine` 当圆/曲线虚线 | 直线虚线用 `DashedLine`；圆/曲线虚线用 `DashedVMobject(Circle(...))` |

## 2. 实测存在的核心 API（可放心用）
- **动画**：`Create` `Write` `FadeIn` `FadeOut` `Transform` `ReplacementTransform` `GrowFromCenter` `DrawBorderThenFill` `Indicate` `Circumscribe`
- **文本/公式**：`Text`（纯文本，零 LaTeX，优先用）、`MarkupText`（带样式纯文本）、`MathTex`（数学公式，走 LaTeX）、`Tex`（LaTeX 文本）
- **几何**：`Polygon` `Circle` `Square` `Rectangle` `Line` `Arrow` `Dot` `Angle` `RightAngle` `DashedLine` `DashedVMobject`（将任意 VMobject 变虚线）
- **坐标/图像**：`Axes` `NumberPlane` `ImageMobject`（图片素材）`SVGMobject`（SVG 素材）
- **容器**：`VGroup`（组合多个 mobject）
- **颜色常量**：`BLUE PURPLE RED GREEN YELLOW WHITE BLACK GREY ORANGE PINK TEAL` 及 `_A/_B/_C/_D/_E` 变体
- **方向常量**：`UP DOWN LEFT RIGHT ORIGIN UL UR DL DR`
- **定位**：`.next_to(m, DIR, buff=)` `.to_edge(DIR)` `.move_to(p)` `.shift(vec)` `.scale(k)` `.set_color(c)`

## 3. LaTeX 注意（本环境完整 TeX 可用，公式可放心渲）
- 公式用 `MathTex(r"a^2 + b^2 = c^2")`，**字符串前加 `r`**（raw）避免转义。
- 多部分：`MathTex(r"a^2", "+", "b^2", "=", "c^2")`，可分段着色 `[i].set_color(...)`。
- 含中文 + 公式时，中文用 `Text`，公式用 `MathTex`，再 `VGroup(...).arrange(RIGHT)`，**不要**把中文塞进 `MathTex`。

## 4. 好看默认（先于"内容"决定观感，详见 base Scene 设计）
- 背景默认深色（manim 默认 `#000000`，本项目 base Scene 会改为靛蓝深色 `#12141D`）。
- 元素别贴边：用 `.to_edge(..., buff=0.5)`；多元素 `VGroup(...).arrange(DOWN, buff=0.4)`。
- 节奏：`run_time` 显式给（0.8~1.5s 常用），段落间 `self.wait(0.5)`。
- 字号：标题 `font_size=64~72`，正文 `36~48`，注释 `24~32`。

## 4.1 ⚠️ 底部给字幕留安全区（硬性要求）
成片会在底部叠加配音字幕，所以**画面最底部约 1 个单位高度必须留空**：
- 画面可用纵向范围按 **y ∈ [-3.0, 3.8]** 安排，**不要把任何元素放到 y < -3.0** 的最底部。
- 标题用 `.to_edge(UP)`；需要放底部的公式/结论用 `.to_edge(DOWN, buff=1.2)`（buff 至少 1.2），别用 `buff=0` 贴底。
- 整组内容偏中上排布，底部那条留给字幕，避免画面文字和字幕重叠。

## 4.2 ⚠️ 精确布局，避免无意义重叠（硬性要求）
- **镜头切换必须清场**：除第一镜外，每个新 shot 开头先 `a2m_clear(self)`（跨镜保留的对象作参数传入：`a2m_clear(self, ax, title)`）。严禁上一镜元素残留着叠画下一镜——多镜元素堆叠是最常见的画面事故。
- **别超出画面**：可用范围 x∈[-6.8, 6.8]、y∈[-3.0, 3.8]。长公式/长文字/大图摆放前先想会不会超边，可能超就 `a2m_fit(对象)`（自动缩到安全区内）或 `.scale(0.8)`。
- 元素间用 `.next_to(对象, 方向, buff=0.3~0.6)` / `VGroup(...).arrange(方向, buff=0.4)` 排布，**给足间距**，不要让本不该重叠的文字/图形互相压到一起。
- 标注文字放在对象**旁边**（next_to）而不是盖在对象上；多个标签彼此也要错开；同屏解说文字最多 2 块，新解说出现前先 FadeOut 旧解说。
- 同屏最多 4 组视觉元素，摆不下就拆成两镜；元素多时先用 VGroup 整体排布再统一 `a2m_fit` 缩放/居中，别各自 move_to 导致挤叠。
- **例外**：确实需要叠加的画面（如把标签贴在图形内、用方框/高亮圈住某元素、文字叠在色块上）不在此限——这类是有意为之，正常做。

## 5. 强约束输出（解析友好）
- 单文件、单 `Scene` 子类、类名用系统给定值。
- 不写 `if __name__ == "__main__"`，不调用 `.render()`，不写命令行——渲染由系统跑。
- 不 `import` 标准库以外的第三方包（除 `manim` `numpy`）。
- 代码按镜头注释分段：`# shot_01：<这段教什么>`（便于后续"定向编辑"与分镜章节定位）。

---
_维护：随 ManimCE 版本升级或发现新坑追加；few-shot 范例须对齐本版本。_
