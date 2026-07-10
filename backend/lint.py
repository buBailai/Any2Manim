"""确定性预检（零 LLM 成本）：机械修正已知错误写法 + 静态揪出"必挂"问题。

heal 循环在进 dry_run 之前调用 preflight()：
- 机械替换：旧版/ManimGL API → CE 写法，直接改代码，不耗费自愈次数；
- 必挂检测（blocking）：MathTex/Tex 里塞中文（LaTeX 编译必失败）、用了不存在的
  名字（NameError）——带精确行号直接喂修复模型，省掉一次 30~60s 的 dry_run 试错。

依据本地失败数据（2026-07）：17 个自愈耗尽的失败版本里 6 个是中文进 MathTex、
7 个是 MathTex 索引/未定义名——都属于渲染前静态可发现的问题。
"""
from __future__ import annotations

import ast
import builtins
import re
from typing import Optional

from . import defaults

# ── 机械替换：确定安全的一比一映射（先长后短，避免子串误伤）──────────
_REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bShowCreationThenFadeOut\b"), "ShowPassingFlash"),
    (re.compile(r"\bShowCreation\b"), "Create"),
    (re.compile(r"\bTexMobject\b"), "MathTex"),
    (re.compile(r"\bTextMobject\b"), "Text"),
    (re.compile(r"\bfrom\s+manimlib\s+import\b"), "from manim import"),
    (re.compile(r"\bimport\s+manimlib\b"), "import manim"),
    (re.compile(r"\.get_graph\("), ".plot("),      # GraphScene 遗风；CE 是 Axes.plot
]

# 已知不存在/必挂的方法调用（AST 难以查属性，逐行正则 + 精确修法提示）
_BAD_METHODS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\.set_dash\s*\("),
     "`.set_dash(...)` 不存在——虚线请改用 DashedVMobject(对象, num_dashes=15) 包一层，"
     "或直线直接用 DashedLine"),
    (re.compile(r"\.set_style\s*\(\s*dash"),
     "`.set_style(dash_length=...)` 不支持 dash 参数——虚线请改用 DashedVMobject(对象, num_dashes=15)"),
    (re.compile(r"\.scale_in_place\s*\("),
     "`.scale_in_place(...)` 不存在——直接用 .scale(...)"),
]


def _bad_method_issues(code: str) -> list[dict]:
    issues = []
    for n, line in enumerate(code.splitlines(), 1):
        for pat, msg in _BAD_METHODS:
            if pat.search(line):
                issues.append({"line": n, "msg": f"第 {n} 行：{msg}。"})
    return issues


def _mechanical(code: str) -> str:
    for pat, rep in _REWRITES:
        code = pat.sub(rep, code)
    return code


# ── CJK 进 MathTex/Tex：LaTeX 编译必挂 ─────────────────────────
_CJK = re.compile(r"[一-鿿　-〿＀-￯]")
# 无 LaTeX 语法的"纯文本"串才可机械转 Text（含反斜杠命令/上下标/花括号的不敢动）
_LATEXY = re.compile(r"[\\^_{}$&%]")


def _tex_string_args(call: ast.Call) -> list[str]:
    return [a.value for a in call.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _fix_cjk_tex(code: str) -> tuple[str, list[dict]]:
    """MathTex/Tex 含 CJK：能机械转 Text 的直接转，其余产出精确 blocking issue。

    机械转换条件：单个字符串位置参数、串里无任何 LaTeX 语法字符 —— 此时
    Text(同参) 完全等价且必渲。多段参数或含 LaTeX 命令的，只报精确行号让模型改。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, []
    lines = code.splitlines()
    issues: list[dict] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("MathTex", "Tex")):
            continue
        strs = _tex_string_args(node)
        if not any(_CJK.search(s) for s in strs):
            continue
        convertible = (len(node.args) == 1 and len(strs) == 1
                       and not _LATEXY.search(strs[0]))
        ln = node.func.lineno - 1
        col = node.func.col_offset
        name = node.func.id
        if convertible and lines[ln][col:col + len(name)] == name:
            lines[ln] = lines[ln][:col] + "Text" + lines[ln][col + len(name):]
        else:
            snippet = "、".join(s for s in strs if _CJK.search(s))[:40]
            issues.append({"line": node.lineno,
                           "msg": f"第 {node.lineno} 行 {name}(...) 里含中文「{snippet}」，"
                                  f"LaTeX 编译必失败——中文只能用 Text()，公式与中文说明"
                                  f"要拆开（公式用 {name}，中文另起 Text 再排版）。"})
    return "\n".join(lines) + ("\n" if code.endswith("\n") else ""), issues


# ── 符号白名单：NameError 提前到渲染前发现 ─────────────────────
_whitelist_cache: Optional[set[str]] = None


def _preamble_names() -> set[str]:
    """从 defaults.PREAMBLE 收集注入的名字（helper/色板/np 等），随 preamble 演进自动跟上。"""
    names: set[str] = set()
    try:
        tree = ast.parse(defaults.PREAMBLE)
    except SyntaxError:
        return names
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _whitelist() -> set[str]:
    """合法全局名 = dir(manim) + preamble 注入 + builtins。manim 导入失败则返回空集（跳过检查）。"""
    global _whitelist_cache
    if _whitelist_cache is None:
        try:
            import manim  # noqa: PLC0415 —— 懒加载：仅首次预检时导入（~1s，进程内缓存）
            _whitelist_cache = set(dir(manim)) | _preamble_names() | set(dir(builtins))
        except Exception:  # noqa: BLE001
            _whitelist_cache = set()
    return _whitelist_cache


def _defined_names(tree: ast.AST) -> set[str]:
    """用户代码里绑定过的名字（赋值/循环变量/函数参/导入/异常名…），不管先后顺序，宁漏勿误报。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "*":
                    continue
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _undefined_names(code: str) -> list[dict]:
    wl = _whitelist()
    if not wl:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    defined = _defined_names(tree)
    issues, seen = [], set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                and node.id not in defined and node.id not in wl
                and node.id not in seen):
            seen.add(node.id)
            issues.append({"line": node.lineno,
                           "msg": f"第 {node.lineno} 行用了不存在的名字 `{node.id}`"
                                  f"（本环境 ManimCE 0.20 没有它，会 NameError）——"
                                  f"换成速查表里确定存在的 API 或删掉。"})
    return issues


def preflight(code: str) -> tuple[str, list[dict]]:
    """渲染前预检：返回 (机械修正后的代码, 必挂问题列表 [{line, msg}])。

    问题列表非空时代码必然渲不过——调用方应跳过 dry_run，把这些精确问题直接喂修复模型。
    """
    code = _mechanical(code)
    code, issues = _fix_cjk_tex(code)
    issues += _undefined_names(code)
    issues += _bad_method_issues(code)
    issues.sort(key=lambda i: i["line"])
    return code, issues
