"""核心引擎：分镜→代码两段式 + 自愈渲染循环（第七、八节）。

generate()：planning → codegen → 自愈循环（ast→dry_run→分类→修），返回"验证可跑"的代码。
"能跑"与"出片"解耦：本模块只保证代码跑通，出缩略图/预览由 jobs 层做。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

from . import config, edits, render
from .llm import BaseLLM

Emit = Callable[..., Awaitable[None]]


# ── 提示词 ──────────────────────────────────────────────────
def _frag(name: str) -> str:
    p = config.PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_cheatsheet() -> str:
    return _frag("manim_api_cheatsheet.md")


# Phase 1 注入片段：教学原则 + 镜头动作语法 + 公式拆解规范
_TEACHING = _frag("teaching_principles.md")
_CAMERA = _frag("a2m_camera_grammar.md")
_FORMULA = _frag("formula_design_rules.md")


# 旧的自由文本分镜（保留作降级兜底说明，现主路径用 SYS_PLAN 产结构化计划）
SYS_STORYBOARD = (
    "你是教学动画分镜师。把老师的一句话需求拆成 3~5 步自然语言分镜计划，"
    "每步说清画面上出现什么、怎么动。只输出分镜，不要写代码。语言简洁。"
)


# 结构化教学镜头计划（先教学后符号；一次调用产可验证合同，codegen 据此翻译）
SYS_PLAN = (
    "你是教学动画的【教学设计 + 镜头规划】师。把老师的一句话需求，规划成一段"
    "「先建立直觉、再引入符号」的教学镜头计划。严格遵守下面的教学原则与镜头动作语法。\n\n"
    f"{_TEACHING}\n\n{_CAMERA}\n\n{_FORMULA}\n\n"
    "只输出一个 JSON 对象（不要 markdown 围栏、不要解释、不要多余文字），结构如下：\n"
    "{\n"
    '  "brief": {"topic": "主题", "audience": "学段/对象", "core_claim": "这条动画要让学生明白的一句话", "final_takeaway": "结尾留给学生的一句话"},\n'
    '  "shots": [\n'
    '    {"id": "shot_01", "teaches": "这段教什么(一句)", "actions": [{"verb": "HEADLINE", "text": "..."}, {"verb": "SHOW", "desc": "画面出现什么"}, {"verb": "CAPTION", "text": "底部解说一句"}], "seconds": 8}\n'
    "  ],\n"
    '  "formulas": [{"latex": "v = g t", "parts": ["v","=","g","t"], "plain": "速度=重力加速度×时间", "terms": [{"i": 0, "means": "速度"}, {"i": 2, "means": "重力加速度"}, {"i": 3, "means": "时间"}]}\n'
    "}\n"
    "规则：3~6 个 shot；第一个 shot 必须含 HEADLINE 动作，最后一个 shot 必须含 TAKEAWAY 动作；"
    "公式必须先有视觉 shot 铺垫，再用 REVEAL_FORMULA；不涉及公式就省略 formulas 字段。"
    "动作的内容写在 text 或 desc 字段里。"
)


def _extract_json(text: str) -> "Optional[dict]":
    """从模型输出里抠出 JSON 对象（去围栏、取最外层 {}）；非法或无 shots 返回 None。"""
    s = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:  # noqa: BLE001
        return None
    if isinstance(obj, dict) and isinstance(obj.get("shots"), list) and obj["shots"]:
        return obj
    return None


def _format_plan(plan: dict) -> str:
    """把结构化计划渲染成既给老师看、又喂 codegen 的文本。"""
    b = plan.get("brief") or {}
    lines: list[str] = []
    topic, aud = (b.get("topic") or "").strip(), (b.get("audience") or "").strip()
    if topic:
        lines.append(f"主题：{topic}" + (f"（面向 {aud}）" if aud else ""))
    if b.get("core_claim"):
        lines.append(f"核心：{b['core_claim']}")
    lines.append("镜头：")
    for idx, sh in enumerate(plan.get("shots") or [], 1):
        sid = sh.get("id") or f"shot_{idx:02d}"
        secs = sh.get("seconds")
        lines.append(f"{idx}. [{sid}｜教：{sh.get('teaches','')}]"
                     + (f"（约 {secs}s）" if secs else ""))
        for a in sh.get("actions") or []:
            content = a.get("text") or a.get("desc") or a.get("description") or ""
            lines.append(f"   - {a.get('verb','')}" + (f"：{content}" if content else ""))
    if plan.get("formulas"):
        lines.append("公式：")
        for f in plan["formulas"]:
            latex = f.get("latex") or " ".join(f.get("parts") or [])
            terms = "、".join(t.get("means", "") for t in (f.get("terms") or []) if t.get("means"))
            seg = f"- {latex}：{f.get('plain','')}"
            lines.append(seg + (f"（逐项：{terms}）" if terms else ""))
    if b.get("final_takeaway"):
        lines.append(f"收尾（TAKEAWAY）：{b['final_takeaway']}")
    return "\n".join(lines)


# 本环境亲测可渲的范例（few-shot）：示范好风格——分步注释、helper、MathTex 正确用法
_FEWSHOT = f'''from manim import *


class {config.SCENE_CLASS_NAME}(Scene):
    def construct(self):
        # --- 第1步：标题 ---
        title = a2m_title("二次函数")
        self.play(Write(title), run_time=1.2)
        self.play(title.animate.to_edge(UP), run_time=0.6)
        # --- 第2步：坐标系与抛物线 ---
        ax = a2m_axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], x_length=6, y_length=4)
        graph = ax.plot(lambda x: x**2, x_range=[-2.2, 2.2], color=A2M_CYAN)
        self.play(Create(ax), run_time=1.0)
        self.play(Create(graph), run_time=1.2)
        # --- 第3步：公式 ---
        eq = MathTex("y = x^2", font_size=48).next_to(ax, RIGHT, buff=0.4)
        self.play(Write(eq), run_time=0.8)
        self.wait(0.6)'''


# 无 LaTeX 版范例：公式用 Text（避免 MathTex）
_FEWSHOT_NOTEX = _FEWSHOT.replace(
    'eq = MathTex("y = x^2", font_size=48)',
    'eq = Text("y = x²", font_size=44)')


def _fewshot() -> str:
    return _FEWSHOT if config.has_latex() else _FEWSHOT_NOTEX


def _assets_hint(asset_names: Optional[list[str]]) -> str:
    if not asset_names:
        return ""
    listing = "、".join(asset_names)
    return (
        "\n本项目老师已上传以下素材，可在动画里引用（强烈建议用上相关的）：\n"
        f"  {listing}\n"
        "  引用方式：图片用 ImageMobject(a2m_asset(\"文件名\"))，"
        "SVG 用 SVGMobject(a2m_asset(\"文件名\"))；可对其 .scale()/.move_to()/.next_to() 定位、"
        "用 FadeIn/Create 让它出现、加标注。\n")


# 稳优先：教学结构保住的前提下，优先可渲染性（v1.0.3 公式过激导致成功率掉，这里拉回）
_ROBUST = (
    "\n【稳优先 · 重要】在保证教学完整的前提下，优先用最稳、最简单的写法，"
    "宁可朴素也要能渲染成功：\n"
    "- 能用基础图形 / 坐标系 / 普通文字表达，就别上复杂构造；\n"
    "- 只用常见且确定存在的 API（Create/Write/FadeIn/FadeOut/Transform/Indicate/Circumscribe 等），"
    "不确定的一律不用；\n"
    "- 避开易翻车写法：复杂 updater、对 MathTex 多部件做下标索引、罕见参数、过深的嵌套动画；\n"
    "- 公式逐项讲解（TERM_TOUR）能稳就做，拿不准就退化为「一条完整公式 + 一句文字解释」，"
    "别为炫技牺牲可渲染性。"
)


def _latex_directive() -> str:
    if config.has_latex():
        return ("\n【公式】本机可渲 LaTeX：公式用 MathTex，但保持简单、别对多部件做复杂下标操作；"
                "中文一律用 Text。")
    return ("\n⚠️【本机无 LaTeX —— 硬性约束】禁止使用 MathTex / Tex（用了会直接渲染失败）！"
            "所有公式改用 Text 写：用 Unicode 上下标（x²、x³、aₙ、x₁）、用 / 表示分数、普通字符拼，"
            "例如 Text(\"v = g·t\")、Text(\"y = x² - 2x - 3\")、Text(\"f(t) = a₀/2 + Σ(...)\")。"
            "复杂公式就用文字描述 + 图形表达，不要强求排版。")


def _sys_codegen(asset_names: Optional[list[str]] = None) -> str:
    return (
        "你是 ManimCE 动画工程师。把下面给出的【教学镜头计划】逐 shot 翻译成可直接渲染的 "
        "ManimCE 动画——你的任务是【忠实翻译这份计划】，把每个动作落成对应画面，而不是自由发挥。\n"
        f"遵守以下教学原则与镜头动作语法：\n\n{_TEACHING}\n\n{_CAMERA}\n\n"
        f"再严格遵守《API 速查/避坑清单》——与之冲突即错误：\n\n{_load_cheatsheet()}\n\n"
        "渲染环境已注入这些好默认与 helper，可直接使用（无需自己定义）：\n"
        "- 背景已是靛蓝深色；色板常量 A2M_ACCENT/A2M_VIO/A2M_CYAN/A2M_INK；\n"
        "- 基础：a2m_title(text)/a2m_caption(text)/a2m_highlight(mobj)/a2m_axes(**kw)/a2m_asset(name)。\n"
        "- 【教学积木，优先用】（自带安全区/对齐，省得自己排版，能少很多布局翻车）：\n"
        "  a2m_headline(text) 置顶标题；a2m_safe_caption(text) 底部安全区解说；\n"
        "  a2m_takeaway(text) 结论卡；a2m_formula_with_caption(latex, 大白话) 公式+注解；\n"
        "  a2m_term_tour(mathtex, [(项下标, 说明), ...]) 公式逐项小注（mathtex 需用 MathTex(\"a\",\"+\",\"b\") 分项）；\n"
        "  a2m_compare_layout(左, 右, 左标题, 右标题) 左右对比；a2m_vt_graph(t_max, v_max) 速度-时间坐标系；\n"
        "  a2m_number_line(...) 数轴；a2m_timeline([标签...]) 横向时间轴。\n"
        "- 镜头动作尽量映射到上述积木：HEADLINE→a2m_headline、CAPTION→a2m_safe_caption、"
        "TAKEAWAY→a2m_takeaway、REVEAL_FORMULA→a2m_formula_with_caption、TERM_TOUR→a2m_term_tour、"
        "COMPARE→a2m_compare_layout、PLOT→a2m_vt_graph/a2m_axes，少从零排版。\n"
        "⚠️【完整翻译，严禁偷懒】必须实现计划里的【每一个 shot 和每一个动作】：\n"
        "- 每个 shot 对应一段代码，开头用注释 `# shot_01：<这段教什么>` 标出，按顺序全部落地，不能跳过任何 shot；\n"
        "- 文字类动作和视觉动作【同等重要、都要实现】：HEADLINE 要真的画出标题、CAPTION 要画出底部解说、"
        "REVEAL_FORMULA 要画出公式（按下方【公式】约束选 MathTex 或 Text）、TAKEAWAY 要画出结论文字；\n"
        "- 严禁「只画最有趣的那个图形（如只画一条曲线）就结束」而省略标题/解说/公式/对比/结论；\n"
        "- 每个 shot 至少有一次 self.play；输出前自检：计划里每个 shot、每个 HEADLINE/CAPTION/公式/TAKEAWAY 是否都在代码里。\n"
        f"{_ROBUST}{_latex_directive()}\n"
        f"{_assets_hint(asset_names)}\n"
        f"参考范例（照此风格）：\n{_fewshot()}\n\n"
        f"只输出一个完整 Python 文件，含 `from manim import *` 和 "
        f"`class {config.SCENE_CLASS_NAME}(Scene)`，不要 markdown 围栏、不要解释。"
    )


SYS_FIX = (
    "你是 ManimCE 调试专家。下面的代码渲染报错了。请修复并返回"
    "【完整可运行代码】（不是 diff、不要 markdown 围栏、不要解释）。\n"
    "⚠️ 只修报错处，**必须保留原有的全部教学镜头与内容**——每个 shot、标题(HEADLINE)、"
    "解说(CAPTION)、公式、逐项讲解、结论(TAKEAWAY)都要原样还在。"
    "严禁为了让它跑通就删减镜头、把整片简化成只画一个图形。\n"
    "常见坑：ShowCreation→Create、TexMobject→MathTex、manimlib→manim、"
    "把中文塞进 MathTex（应改用 Text）。"
)

SYS_TEACH_REPAIR = (
    "你是教学动画质检 + 修复师。下面这段 ManimCE 代码能渲染，但有教学质量问题。"
    "请按【教学镜头计划】和指出的【问题】改进，返回【完整可运行代码】"
    "（不是 diff、不要 markdown 围栏、不要解释）。\n"
    "保留原有能用的部分，重点补齐缺失的视觉对象/动画/结论，"
    "**只能让内容更完整、更像教学，绝不能简化成更少内容**。"
    "优先用已注入的教学积木（a2m_headline/a2m_safe_caption/a2m_takeaway/"
    "a2m_formula_with_caption/a2m_compare_layout/a2m_vt_graph 等）。"
)


def _teach_repair_prompt(code: str, plan: str, issues: "list[dict]") -> str:
    issue_text = "\n".join(f"- {i['msg']}" for i in issues)
    plan_block = f"教学镜头计划：\n{plan}\n\n" if plan else ""
    return (f"{plan_block}发现的教学问题：\n{issue_text}\n\n"
            f"当前代码：\n{code}\n\n请返回改进后的完整代码。")


SYS_EDIT = (
    "你是 ManimCE 定向编辑器。给你当前完整代码与老师的修改要求，"
    "只输出【最小改动的 search/replace 块】，可多个，格式严格：\n"
    "<<<<<<< SEARCH\n<精确粘贴要替换的旧片段>\n=======\n<新片段>\n>>>>>>> REPLACE\n"
    "SEARCH 必须与原代码逐字一致（含缩进）。不要输出完整代码、不要解释、不要 markdown 围栏。"
    "只改与要求相关的部分，其余原样不动。"
)


# ── 错误分类（第七节 B-3）──────────────────────────────────
class ErrClass(Enum):
    CODE = "code"     # 可修的代码错 → 喂回 LLM 重试
    HEAVY = "heavy"   # 超时/过重 → 走简化路径
    ENV = "env"       # 环境缺依赖 → 立即跳出，不耗 LLM


# 仅匹配"二进制/模块真的缺失"——而非 LaTeX 编译报错。
# 关键区分：latex 跑起来但编译失败("latex error converting to dvi"/"LaTeX Error: Unicode")
# 是【代码问题】（如中文塞进 MathTex），应让 LLM 修；只有 latex/ffmpeg 可执行文件本身
# 不存在（FileNotFoundError 等）才是【环境缺依赖】。
_ENV_PAT = re.compile(
    r"No module named"
    r"|No such file or directory"
    r"|FileNotFoundError"
    r"|command not found"
    r"|not found in (?:your )?\$?PATH"
    r"|could not find (?:the )?(?:executable|binary|ffmpeg|dvisvgm|latex|pdflatex)"
    r"|(?:latex|ffmpeg|dvisvgm|pdflatex|pango|cairo) (?:is )?not (?:installed|found|available)"
    r"|please install",
    re.IGNORECASE)


def classify_error(tb: str, timed_out: bool = False) -> ErrClass:
    if timed_out:
        return ErrClass.HEAVY
    if _ENV_PAT.search(tb):
        return ErrClass.ENV
    return ErrClass.CODE


def _err_signature(tb: str) -> str:
    """归一化错误签名：去行号/路径/十六进制，用于"同错连续"判定。"""
    s = re.sub(r"line \d+", "line N", tb)
    s = re.sub(r"0x[0-9a-fA-F]+", "0xH", s)
    s = re.sub(r"/[^\s'\"]+", "/P", s)
    m = re.findall(r"\w*(?:Error|Exception)[:]\s?.*", s)
    return (m[-1] if m else s)[:160].strip()


# ── 结果 ────────────────────────────────────────────────────
@dataclass
class GenResult:
    ok: bool
    code: str
    storyboard: str = ""
    attempts: int = 0
    error: str = ""              # 失败时给老师的大白话
    env_missing: bool = False    # 环境缺依赖（区别于代码错）
    warnings: list = field(default_factory=list)  # 教学质量提示（非阻塞，记录用）


def _fix_prompt(code: str, tb: str, intent: str, plan: str = "") -> str:
    plan_block = f"教学镜头计划（必须完整保留其全部镜头与内容）：\n{plan}\n\n" if plan else ""
    return (f"原始意图：{intent}\n\n{plan_block}出错代码：\n{code}\n\n"
            f"精简报错：\n{tb}\n\n请返回修复后的完整代码：保留全部镜头与教学内容，只改出错处。")


async def heal(code: str, intent: str, llm: BaseLLM, emit: Emit,
               attempt0: int = 0, plan: str = "") -> GenResult:
    """自愈循环：有界、会喊停、保住最后一个能渲的版本。

    时间预算从本函数开始计时（只算"验证+修"的耗时），不含上游 LLM 生成代码的延迟——
    否则真实(慢)模型生成完，预算就已耗尽，heal 会 0 次尝试直接退出。
    """
    start = time.time()
    last_good: Optional[str] = None
    seen: list[str] = []
    attempt = attempt0

    while attempt < config.HEAL_MAX_ATTEMPTS:
        # 硬预算：累计时间
        if time.time() - start > config.HEAL_MAX_SECONDS:
            break

        # 1) 静态语法校验（秒级，不进渲染）
        ok, serr = render.syntax_check(code)
        if not ok:
            sig = _err_signature(serr)
            seen.append(sig)
            if seen.count(sig) >= config.HEAL_SAME_ERROR_STOP:
                break
            attempt += 1
            await emit("healing", attempt=attempt, reason="修正语法")
            fixed = render.extract_code(
                llm.complete(SYS_FIX, _fix_prompt(code, serr, intent, plan),
                             task="fix", temperature=0.3))
            if fixed.strip():
                code = fixed          # 修复抽不出代码就保留原码，绝不清空
            continue

        # 2) 验证渲染（dry_run，只构建不出片）
        if code.strip():
            last_good = code          # 语法已过且非空 → 作为失败时的兜底代码
        await emit("verifying", attempt=attempt)
        res = await render.dry_run(code)
        if res.ok:
            return GenResult(True, code=code, attempts=attempt)

        # 3) 分类
        cls = classify_error(res.traceback, res.timed_out)
        if cls is ErrClass.ENV:
            return GenResult(False, code=last_good or code, attempts=attempt,
                             error="渲染环境缺少依赖（如 LaTeX/ffmpeg），不是代码问题。",
                             env_missing=True)

        sig = _err_signature(res.traceback)
        seen.append(sig)
        if seen.count(sig) >= config.HEAL_SAME_ERROR_STOP:
            break

        attempt += 1
        reason = "简化场景" if cls is ErrClass.HEAVY else "修正代码"
        await emit("healing", attempt=attempt, reason=reason)
        extra = ("\n注意：场景过重/超时，请精简每个镜头的实现（减少元素数量、缩短动画时长），"
                 "但仍保留全部 shot 与教学内容，不要删掉镜头。") if cls is ErrClass.HEAVY else ""
        fixed = render.extract_code(
            llm.complete(SYS_FIX, _fix_prompt(code, res.traceback, intent, plan) + extra,
                         task="fix", temperature=0.3))
        if fixed.strip():
            code = fixed          # 同理：修复无产出就保留原码

    return GenResult(False, code=last_good or code, attempts=attempt,
                     error="多次尝试仍未渲染成功。可以换个说法重述、退回上一版本、或查看代码手动调。")


async def generate(description: str, llm: BaseLLM, emit: Emit,
                   prior_code: Optional[str] = None,
                   asset_names: Optional[list[str]] = None) -> GenResult:
    """主入口：结构化教学规划 → 翻译成代码 → 自愈。prior_code 作为失败兜底锚点。

    规划改为一次产出结构化教学镜头计划（JSON）；解析失败则把原文当自由文本分镜兜底
    （保护弱模型用户），整条链路仍只 2 次 LLM 调用，不增延迟。
    """
    await emit("planning")
    try:
        raw_plan = llm.complete(SYS_PLAN + _latex_directive(), f"老师的需求：{description}",
                                task="plan", temperature=0.4)
    except Exception as e:  # noqa: BLE001
        return GenResult(False, code=prior_code or "", error=f"LLM 调用失败：{e}")

    plan_dict = _extract_json(raw_plan)
    storyboard = _format_plan(plan_dict) if plan_dict else raw_plan.strip()

    await emit("storyboard", text=storyboard)

    # 生成代码：把教学镜头计划逐 shot 翻译成 Manim
    await emit("generating")
    try:
        raw = llm.complete(_sys_codegen(asset_names),
                           f"需求：{description}\n\n教学镜头计划：\n{storyboard}",
                           task="codegen", temperature=0.2)
    except Exception as e:  # noqa: BLE001
        return GenResult(False, code=prior_code or "", storyboard=storyboard,
                         error=f"LLM 调用失败：{e}")
    code = render.extract_code(raw)
    await emit("code_draft", code=code)   # 提前把代码亮出来，等待时「代码」tab 看得到产出

    # 自愈（带上教学镜头计划，修报错时不至于把镜头简化掉）
    result = await heal(code, description, llm, emit, plan=storyboard)
    result.storyboard = storyboard
    if result.ok:
        await _teach_pass(result, description, storyboard, llm, emit)  # 「能教」质检 + 一次教学修复
    elif not result.code and prior_code:
        result.code = prior_code   # 极端情况(没产出任何代码)才退回上一版
    # 失败时【保留这次尝试的代码】，便于老师在此基础上继续改（上一个能渲版本仍在时间线里可回退）
    return result


async def _teach_pass(result: GenResult, intent: str, plan: str,
                      llm: BaseLLM, emit: Emit) -> None:
    """对已渲通过的代码做「能教」静态检查：block 级做一次教学修复（有界、失败回滚），warn 级仅记录。"""
    issues = render.teachability_issues(result.code, intent)
    result.warnings = [i["msg"] for i in issues if i.get("severity") == "warn"]
    blocking = [i for i in issues if i.get("severity") == "block"]
    if not blocking:
        return
    await emit("teach_repair", issues=[i["key"] for i in blocking])
    try:
        raw = llm.complete(SYS_TEACH_REPAIR,
                           _teach_repair_prompt(result.code, plan, blocking),
                           task="fix", temperature=0.3)
    except Exception:  # noqa: BLE001
        return
    code2 = render.extract_code(raw)
    ok, _ = render.syntax_check(code2)
    if not ok:
        return
    v = await render.dry_run(code2)
    # 仅当修复版能渲、且不再有 block 级问题才采用，否则保留原能渲版本
    if v.ok and not [i for i in render.teachability_issues(code2, intent)
                     if i.get("severity") == "block"]:
        result.code = code2


async def edit(prior_code: str, instruction: str, llm: BaseLLM, emit: Emit) -> Optional[GenResult]:
    """定向编辑：只产最小改动块，应用后走快校验+自愈。

    返回 None 表示无法定向编辑（无块/匹配不上）→ 调用方降级整段重生成。
    """
    await emit("editing")
    try:
        raw = llm.complete(SYS_EDIT,
                           f"当前代码：\n{prior_code}\n\n修改要求：{instruction}",
                           task="edit", temperature=0.2)
    except Exception:  # noqa: BLE001
        return None

    blocks = edits.parse_blocks(raw)
    if not blocks:
        return None
    er = edits.apply_blocks(prior_code, blocks)
    if er.applied == 0:          # SEARCH 全部匹配不上 → 降级
        return None

    await emit("edited", applied=er.applied, blocks=er.blocks)
    result = await heal(er.code, instruction, llm, emit)
    if not result.ok:
        result.code = prior_code  # 编辑改坏了也保住上一版
    return result


SYS_NARRATE = (
    "你是教学视频旁白撰稿人。根据这个教学动画【最终呈现的内容】写中文旁白——把动画教的知识点讲清楚。\n"
    "⚠️ 重要：输入里若出现【制作调整指令】（如「改成蓝色」「放慢一点」「调整位置」「弧线不对」「字大一点」"
    "「换个角度」），这些是做动画时的修改过程，不是教学内容——绝不要把「修改/调整这件事」写进旁白"
    "（不能说「我们把它改成了蓝色」「这里放慢」「调整了位置」之类）。\n"
    "（但用颜色/位置来指代画面里的元素是可以的，例如「蓝色的这条边代表 a」——这是帮学生看懂画面，没问题。）\n"
    "配合画面讲解：自然口语、不念代码、不要舞台说明或括号注释。\n"
    "格式：直接输出旁白文本，**不要写步骤编号**（不要「第1步：」「步骤一：」之类），按画面顺序自然衔接即可。\n"
    "⏱ 节奏：下面会给出【每段字数上限】和【总字数上限】，这是最高硬约束——"
    "**每段旁白都不能超过它的字数上限，整段不能超过总字数上限**。"
    "宁可精炼简短，也绝不要写超，避免配音比视频长。某段画面短就一句带过，长就多讲两句。\n"
    "直接输出旁白文本。"
)


def _format_narration(text: str) -> str:
    """按句末标点拆行——让文本框/字幕一眼看出断句（每句一行）。"""
    parts = re.split(r"(?<=[。！？!?])", text.strip())
    return "\n".join(p.strip() for p in parts if p.strip())


def estimate_durations(code: str) -> list[tuple[str, float]]:
    """从 manim 代码估算每个 `# 步骤` 段的时长（秒）：run_time + wait + 默认。"""
    sections: list[tuple[str, list[str]]] = []
    label, buf = "开场", []
    for ln in code.splitlines():
        if "# ---" in ln or re.search(r"#\s*第.*步", ln):
            if buf:
                sections.append((label, buf))
            label = re.sub(r"^[\s#\-]+|[\s#\-]+$", "", ln) or f"第{len(sections) + 1}段"
            buf = []
        else:
            buf.append(ln)
    if buf:
        sections.append((label, buf))

    out: list[tuple[str, float]] = []
    for lab, sl in sections:
        t = "\n".join(sl)
        secs = sum(float(m) for m in re.findall(r"run_time\s*=\s*([\d.]+)", t))
        plays = len(re.findall(r"self\.play\(", t))
        rts = len(re.findall(r"run_time\s*=", t))
        secs += max(0, plays - rts) * 1.0            # 未写 run_time 的 play 默认 1s
        secs += sum(float(m) for m in re.findall(r"self\.wait\(\s*([\d.]+)\s*\)", t))
        secs += len(re.findall(r"self\.wait\(\s*\)", t)) * 1.0
        if secs > 0:
            out.append((lab, round(secs, 1)))
    return out


# 中文配音实测速度（含公式/数字读得慢 + 句间停顿 + 字幕 lead-in）约 3.4 字/秒。
# 取偏保守值，让配音宁可略短于视频（留白可接受），也别超出导致末帧冻结补时。
CHARS_PER_SEC = 3.4


def narrate(prompt: str, storyboard: str, llm: BaseLLM,
            code: str = "", total_dur: float = 0.0) -> str:
    durs = estimate_durations(code) if code else []
    raw_total = sum(d for _, d in durs)
    if total_dur and raw_total:        # 按真实总时长缩放各段估算，使其加总吻合
        durs = [(l, round(d / raw_total * total_dur, 1)) for l, d in durs]
        shown_total = total_dur
    else:
        shown_total = total_dur or raw_total

    # 时长 → 字数预算（核心：给 AI 可执行的硬上限，避免旁白过长/过短）
    # 留 0.9x 安全余量：LLM 普遍超标 ~10-20%，给略低一些的上限补偿
    safe = 0.9
    total_cap = int(shown_total * CHARS_PER_SEC * safe) if shown_total else 0
    budget = "\n".join(
        f"- {l}（约 {d} 秒）：≤ {max(4, int(d * CHARS_PER_SEC * safe))} 字"
        for l, d in durs) or "（无法估算分段，按总字数控制即可）"
    cap_line = (f"⏱ 全片约 {round(shown_total, 1)} 秒，"
                f"**旁白总字数必须 ≤ {total_cap} 字**（这是硬上限，宁可少不要超）。\n"
                if total_cap else "")
    user = (f"动画主题：{prompt}\n\n分镜：\n{storyboard or '（无）'}\n\n"
            f"{cap_line}各分镜的字数预算（讲解尽量贴合，不要超）：\n{budget}\n\n"
            f"再次强调：每段旁白别超它的字数上限，整段别超总字数上限，确保配音时长不超过视频。")
    try:
        raw = llm.complete(SYS_NARRATE, user, task="narrate", temperature=0.5).strip()
        return _format_narration(raw)
    except Exception:  # noqa: BLE001
        return ""


async def respond(description: str, llm: BaseLLM, emit: Emit,
                  prior_code: Optional[str] = None,
                  asset_names: Optional[list[str]] = None) -> GenResult:
    """统一入口：有可用旧代码→优先定向编辑（失败再整段重生成）；否则直接生成。"""
    if prior_code:
        edited = await edit(prior_code, description, llm, emit)
        if edited is not None and edited.ok:
            return edited
        await emit("regenerating")   # 定向编辑没成 → 降级整段重生成
    return await generate(description, llm, emit, prior_code=prior_code,
                          asset_names=asset_names)
