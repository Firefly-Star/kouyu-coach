# -*- coding: utf-8 -*-
import json
import sys

import analyzer
from make_samples import TEXT

CASES = ["good", "stall", "slow", "fast", "flat", "skip"]
rows = []
for n in CASES:
    with open("samples/synthetic_%s.wav" % n, "rb") as f:
        r = analyzer.analyze(f.read(), TEXT)
    m = r["metrics"]
    rows.append({
        "case": n,
        "score": r["score"],
        "sub": r["subscores"],
        "rate": m["整体语速_字每秒"],
        "rate_artic": m["吐字速率_字每秒"],
        "syl": "%d/%d" % (m["检测音节数"], m["期望音节数"]),
        "speech": m["说话占比"],
        "long": m["长停顿次数_0.6s以上"],
        "hes": m["犹豫停顿_0.3至0.6s"],
        "jitter": m["音节间隔抖动"],
        "f0": m["基频中值Hz"],
        "range": m["基频起伏_半音"],
        "flat": m["句末平拖占比"],
        "vol": m["音量波动dB"],
    })
out = json.dumps(rows, ensure_ascii=False, indent=1, default=str)
open("test_report.txt", "w", encoding="utf-8").write(out)

# 打印成一行一条，方便直接看趋势
for r in rows:
    print("%-6s 总分%5.1f 流畅%5.1f 准确%5.1f 语速分%5.1f 语调%5.1f | 整体%.2f 吐字%.2f 字/秒 说话%.0f%% 停顿%d 长停顿%d 抖动%.2f 起伏%.1f半音" % (
        r["case"], r["score"],
        r["sub"].get("流畅度", 0), r["sub"].get("读音准确度", r["sub"].get("语流完整度", 0)),
        r["sub"].get("语速", 0), r["sub"].get("语调", 0),
        r["rate"], r["rate_artic"], r["speech"] * 100,
        r["hes"], r["long"], r["jitter"], r["range"]))
print("ok")
