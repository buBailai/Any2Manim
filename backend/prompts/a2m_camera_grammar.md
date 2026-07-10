# Any2Manim 镜头动作语法（规划用动作词；代码生成把它翻译成 ManimCE）

规划时每个 shot 由若干「动作」组成。动作词与它在 ManimCE 的常用译法：

| 动作 | 含义 | ManimCE 译法（参考） |
|---|---|---|
| HEADLINE | 新概念大标题点题 | `a2m_headline(text)` + `Write` |
| SHOW | 对象登场 | `Create` / `FadeIn` / `GrowFromCenter` |
| LABEL | 给对象贴标签（在旁边，不盖住） | `Text(...).next_to(obj, DIR, buff)` |
| CAPTION | 底部一句解说（字幕安全区上方） | `a2m_safe_caption(text)` |
| FOCUS | 高亮强调某对象 | `a2m_highlight` / `Indicate` / `Circumscribe` |
| MOVE | 对象移动 | `obj.animate.shift / move_to` |
| TRANSFORM | 形变 / 替换 | `Transform` / `ReplacementTransform` |
| PLOT | 画图像 / 曲线 | `a2m_axes(...)` / `a2m_vt_graph(...)` + `ax.plot(...)` |
| REVEAL_FORMULA | 揭示公式（必须先有视觉铺垫） | `a2m_formula_with_caption(latex, 大白话)`（或 `MathTex(...)` + `Write`） |
| TERM_TOUR | 按公式各项逐项讲解 | `MathTex("a","+","b")` 分项后 `a2m_term_tour(f, [(i,"说明")...])` + 逐项 `Indicate` |
| COMPARE | 左右 / 上下对比 | `a2m_compare_layout(左, 右, 左标题, 右标题)` |
| PAUSE | 停顿 | `self.wait(t)` |
| CLEAR | 镜头清场（上一镜元素退场） | `a2m_clear(self, 需保留的对象...)` |
| TAKEAWAY | 最终结论 | `a2m_takeaway(text)` |

**铁律**：HEADLINE 开场、TAKEAWAY 收尾各至少一次；公式前必须有视觉铺垫；中文用 `Text`、公式用 `MathTex`；底部字幕安全区（y < -3.0）不可占；**除第一镜外每个 shot 开头必须清场（`a2m_clear(self)`，确需跨镜保留的对象作参数传入），严禁上一镜元素残留着叠画下一镜**；每镜同屏视觉元素 ≤ 4 组，摆不下就拆镜；逐 shot 在代码里用注释 `# shot_01：<teaches>` 标出，便于后续定向编辑。
