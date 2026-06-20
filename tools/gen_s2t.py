"""生成前端简→繁词表 frontend/s2t.js（客户端简繁切换用）。

UI 文案改动后重新跑一次即可：
    pip install opencc-python-reimplemented
    python tools/gen_s2t.py

做法：从 index.html + app.js 抽取所有「汉字连续段」，用 OpenCC s2twp（词组级、
台湾用词）逐段转繁，只保留转换后不同的，落成 window.__S2T={...}。运行时只转
可见文本节点，不碰 JS 逻辑/表单值，故安全。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FE = ROOT / "frontend"
CJK = re.compile(r"[一-鿿]+")


def main() -> int:
    try:
        from opencc import OpenCC
    except Exception:  # noqa: BLE001
        print("缺 opencc：pip install opencc-python-reimplemented", file=sys.stderr)
        return 1
    src = "\n".join((FE / f).read_text(encoding="utf-8") for f in ("index.html", "app.js"))
    cc = OpenCC("s2twp")
    # 1) 词组级：源码里出现的「汉字连续段」整段转，保证 视频质量→影片質量 这类用词地道
    runs = sorted(set(CJK.findall(src)))
    d = {r: cc.convert(r) for r in runs}
    # 2) 单字级兜底：覆盖常用汉字全表（U+4E00–U+9FA5），让任何运行期文本（含服务端消息）
    #    在词组没命中时也能逐字转。运行时贪婪最长匹配 → 词组优先、单字兜底。
    for cp in range(0x4E00, 0x9FA6):
        ch = chr(cp)
        if ch not in d:
            d[ch] = cc.convert(ch)
    d = {k: v for k, v in d.items() if k != v}
    out = FE / "s2t.js"
    out.write_text(
        "window.__S2T=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"runs={len(runs)} mapped={len(d)} -> {out} ({out.stat().st_size}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
