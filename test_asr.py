# -*- coding: utf-8 -*-
"""ASR 验收：转写一段已知文本的合成音频，看字错率

用 samples/demo.wav（内置示例）跑一遍。找不到就对 synthetic_good.wav 跑。
"""
import os
import sys
import time

import asr, content

HERE = os.path.dirname(os.path.abspath(__file__))

print("== 环境 ==")
st = asr.status()
print("  %s" % st)
if not st["available"]:
    print("faster-whisper 未安装，跳过")
    sys.exit(0)

target = os.path.join(HERE, "samples", "demo.wav")
if not os.path.isfile(target):
    target = os.path.join(HERE, "samples", "synthetic_good.wav")
print("\n== 转写 %s ==" % os.path.basename(target))
t0 = time.time()
r = asr.transcribe(target)
el = time.time() - t0
print("  耗时 %.1fs" % el)
if not r.get("ok"):
    print("  失败: %s" % r.get("error"))
    sys.exit(1)
print("  语言: %s  时长: %ss" % (r.get("language"), r.get("duration")))
print("  文本: %s" % r["text"][:120])
print("  分段数: %d" % len(r["segments"]))
for s in r["segments"][:5]:
    print("    %.2f-%.2f  %s" % (s["start"], s["end"], s["text"]))

print("\n== 内容层跑通 ==")
c = content.analyze_text(r["text"], r.get("duration"))
if not c.get("ok"):
    print("  转写为空：内容层返回 %s" % c)
    print("  （demo.wav 是合成正弦信号，whisper 认不出是正常的）")
else:
    print("  字数: %s  填充词: %s  重复: %d" % (
        c["chars"], c["fillers"]["total"], len(c["repeats"])))
    print("  结构: %s" % c["structure"])

print("\n== 卡壳点对齐 ==")
stalls = [{"t": 2.0, "dur": 0.8}, {"t": 5.0, "dur": 0.7}]
al = asr.align_stalls(stalls, r["segments"])
for a in al:
    print("  %.1fs -> %s 「%s」" % (a["t"], a.get("where_text", "?"), a.get("next_text", "")))
print("\nOK")
