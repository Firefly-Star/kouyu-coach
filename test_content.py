# -*- coding: utf-8 -*-
"""内容层验收脚本：填充词、重复、结构校验

用法：python test_content.py
"""
import sys

import content

FAILS = []
CHECKS = [0]


def check(cond, msg):
    CHECKS[0] += 1
    if cond:
        print("  OK   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


print("== 1. 填充词统计 ==")
t1 = "然后我觉得这个问题吧，就是说我其实也没想清楚，那个方案到底行不行，嗯，就是这种感觉。"
f = content.count_fillers(t1)
print("   统计: empty=%s connect=%s total=%d" % (f["empty"], f["connect"], f["total"]))
check(f["total"] >= 4, "识别出 >=4 个填充词（实得 %d）" % f["total"])
check("然后" in f["connect"], "抓到「然后」")
check("就是" in f["connect"], "抓到「就是」")
check("那个" in f["empty"] or "那个" in f["connect"], "抓到「那个」")

print("\n== 2. 干净文本不该误报 ==")
t2 = "先说结论，这件事值得做。原因有两个：时机合适，成本可控。"
f2 = content.count_fillers(t2)
print("   统计: %s" % f2)
check(f2["total"] == 0, "干净文本填充词为 0（实得 %d）" % f2["total"])

print("\n== 3. 自我重复 ==")
t3 = ("这个方案的核心思路是先把范围收窄。"
      "然后我们再看，这个方案的核心思路是先把范围收窄。"
      "所以我觉得，这个方案的核心思路是先把范围收窄。")
rep = content.repeat_phrases(t3)
print("   重复: %s" % [(x["text"][:16], x["count"]) for x in rep[:3]])
check(len(rep) >= 1, "识别出重复片段")
check(any(x["count"] >= 2 for x in rep), "重复次数正确")

print("\n== 4. 结构校验：说了三点实际两点 ==")
t4 = "我想说三点。第一，时间不够。第二，人手不足。就这样。"
st = content.check_structure(t4)
print("   %s" % st)
check(st["claimed"] == 3, "识别出声称 3 点（实得 %s）" % st["claimed"])
check(st["delivered"] == 2, "识别出实际 2 点（实得 %s）" % st["delivered"])
check("实际展开 2" in (st["verdict"] or ""), "结论指出少了一点")

print("\n== 5. 结构校验：说了三点讲满三点 ==")
t5 = "我想说三点。第一，时间。第二，人手。第三，预算。"
st5 = content.check_structure(t5)
print("   %s" % st5)
check(st5["claimed"] == 3 and st5["delivered"] == 3, "三点对三点")
check("讲满" in (st5["verdict"] or ""), "结论是讲满了")

print("\n== 6. 结构校验：说了三点但没展开 ==")
t6 = "我今天想说三点，但是时间有限我们就先聊到这儿。"
st6 = content.check_structure(t6)
print("   %s" % st6)
check(st6["claimed"] == 3 and st6["delivered"] is None, "没有「第一第二」标记")

print("\n== 6b. 「第一句/第二句」不算列举（真实录音里踩过）==")
# 这条来自真实转写：说话人在描述"嘴里讲第一句、脑子想第二句"，
# 旧正则把它当成在列举两点，误报「讲了 2 点」。
t6b = "我们一边说一边想，嘴巴里再讲第一句，脑子里还在找第二句。"
st6b = content.check_structure(t6b)
print("   %s" % st6b)
check(st6b["delivered"] is None, "「第一句/第二句」不被当成列举（实得 %s）" % st6b["delivered"])

print("\n== 6c. 带分隔符的列举才算 ==")
t6c = "第一，时间不够。第二，人手不足。第三、预算也砍了。"
st6c = content.check_structure(t6c)
print("   %s" % st6c)
check(st6c["delivered"] == 3, "「第一，/第二，/第三、」算 3 点（实得 %s）" % st6c["delivered"])

print("\n== 7. 实词占比 ==")
t7 = "这个东西那个东西然后就是说嗯这个东西"
t8 = "预算被砍了一半，所以计划拆成两步走，先验证核心假设。"
c7, c8 = content.content_words(t7), content.content_words(t8)
print("   空话段: %s" % c7)
print("   实义段: %s" % c8)
check(c8["ratio"] > c7["ratio"], "实义段的实词占比更高（%s > %s）" % (c8["ratio"], c7["ratio"]))

print("\n== 8. 总入口：空文本不崩 ==")
r = content.analyze_text("", None)
check(r["ok"] is False, "空文本 ok=False")
r2 = content.analyze_text("随便说两句测试一下内容分析。", 30)
check(r2["ok"] is True, "正常文本 ok=True")
check("fillers" in r2 and "structure" in r2 and "content" in r2, "返回结构完整")

print()
if FAILS:
    print("失败 %d 项：" % len(FAILS))
    for x in FAILS:
        print("  - " + x)
    sys.exit(1)
print("全部通过（%d 项检查）" % CHECKS[0])
