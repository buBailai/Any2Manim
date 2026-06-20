"""定向编辑（第九节）：search/replace 块编辑（Aider 风格）。

小改只输出最小改动块，精确匹配后只替换那几行 —— 更快、更省 token、更不易回归。
匹配不上则降级（由 engine 决定重写受影响段 / 整段重生成）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_RE = re.compile(
    r"<{3,}\s*SEARCH\s*\n(.*?)\n={3,}\s*\n(.*?)\n>{3,}\s*REPLACE",
    re.DOTALL)


@dataclass
class EditResult:
    code: str
    applied: int
    failed: int
    blocks: int


def parse_blocks(text: str) -> list[tuple[str, str]]:
    out = []
    for m in BLOCK_RE.finditer(text):
        out.append((m.group(1), m.group(2)))
    return out


def _normalize(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.splitlines())


def apply_blocks(code: str, blocks: list[tuple[str, str]]) -> EditResult:
    applied = failed = 0
    new = code
    for search, replace in blocks:
        if search and search in new:
            new = new.replace(search, replace, 1)
            applied += 1
            continue
        # 容差：忽略行尾空白再试
        ns, nn = _normalize(search), _normalize(new)
        if search and ns and ns in nn:
            new = nn.replace(ns, _normalize(replace), 1)
            applied += 1
        else:
            failed += 1
    return EditResult(code=new, applied=applied, failed=failed, blocks=len(blocks))
