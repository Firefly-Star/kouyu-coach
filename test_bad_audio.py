# -*- coding: utf-8 -*-
"""验收：坏音频必须返回结构化错误，绝不能抛异常 / 500。"""
import analyzer

cases = {
    "空字节": b"",
    "太短": b"RIFF",
    "非 WAV": b"hello world this is not audio at all, just text bytes....",
    "RIFF 头但截断": b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x10\x00\x00\x00",
    "只有头没有数据": b"RIFF" + b"\x00" * 40,
}

for name, data in cases.items():
    try:
        r = analyzer.analyze(data, "测试文本")
        good = (r.get("ok") is False) and bool(r.get("error"))
        print("%-14s -> 正确拒绝=%s  error=%s" % (name, good, r.get("error", "")[:40]))
    except Exception as e:
        print("%-14s -> 抛异常了！%s: %s" % (name, type(e).__name__, e))

# 正常音频必须仍然通过
d = open("samples/synthetic_good.wav", "rb").read()
r = analyzer.analyze(d, "大家好我说两句吧，今天想和大家聊一聊。其实最重要就是练习。")
print("%-14s -> ok=%s score=%s" % ("正常音频", r.get("ok"), r.get("score")))
