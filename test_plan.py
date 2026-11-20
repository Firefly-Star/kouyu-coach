# -*- coding: utf-8 -*-
"""验收脚本：跑一遍检查计划/素材/题库的结构完整性。

用法：python test_plan.py

它拦的是这几类回归（都真实发生过）：
1. 动作缺 check 字段 → 页面上标不出「可验/自查」
2. 周表漏动作 → 用户按周表练，那个动作永远练不到
3. 素材里的重音标记污染音节计数 → 期望音节数虚高、覆盖率整体偏低
4. 阶段阈值不是递减的 → 某一关谁都过不了，或某一关默认就过
5. 题库为空或取模越界
"""
import re
import sys

import analyzer
import passages
import plan
import topics

FAILS = []
CHECKS = [0]


def check(cond, msg):
    CHECKS[0] += 1
    if cond:
        print("  OK   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


print("== 1. 每个动作都要有 check（可验/自查）==")
ids = {}
for s in plan.STAGES:
    for x in s["sessions"]:
        ids[x["id"]] = x
check(len(ids) >= 13, "动作数 %d >= 13" % len(ids))
missing = [i for i, x in ids.items() if x.get("check") not in ("auto", "self")]
check(not missing, "所有动作都有合法 check%s" % ("" if not missing else " —— 缺: %s" % missing))
no_note = [i for i, x in ids.items() if x.get("check") == "auto" and not x.get("check_note")]
check(not no_note, "auto 动作都写了 check_note%s" % ("" if not no_note else " —— 缺: %s" % no_note))

print("\n== 2. 周表必须覆盖所有动作 ==")
used = set()
for r in plan.WEEKLY_RHYTHM:
    used |= set(re.findall(r"s\d-\d", r["plan"]))
check(not (set(ids) - used), "没有动作被周表漏掉%s" % (
    "" if not (set(ids) - used) else " —— 漏: %s" % sorted(set(ids) - used)))
check(not (used - set(ids)), "周表没引用不存在的动作%s" % (
    "" if not (used - set(ids)) else " —— 多: %s" % sorted(used - set(ids))))

print("\n== 3. 重音标记不能污染音节计数 ==")
for p in passages.ALL_READ:
    raw = p.get("text_raw")
    if raw is None:
        check(False, "%s 缺 text_raw" % p["id"])
        continue
    if any(m in raw for m in analyzer.MARK):
        n1 = sum(1 for c, s in analyzer.pinyin_syllables(raw) if s)
        n2 = sum(1 for c, s in analyzer.pinyin_syllables(p["text"]) if s)
        check(n1 == n2, "%s 标记前后音节数一致 (%d/%d)" % (p["id"], n1, n2))
leftover = [p["id"] for p in passages.ALL_READ if any(m in p["text"] for m in analyzer.MARK)]
check(not leftover, "text 字段里没有残留标记%s" % ("" if not leftover else " —— %s" % leftover))

print("\n== 4. 阶段阈值必须是递减的（越往后越难过）==")
# 用"该阶段的通过条件里，各维度相对满分的严格程度"做粗判：
# 四阶段的要求应当整体上越来越难，而不是第三关比第一关容易。
# 这里退一步只检查一件确定的事：每个维度的 target 单调不降。
prev_target = {}
ok_mono = True
for s in plan.STAGES:
    for k, t in s["pass"].items():
        if k in prev_target and t < prev_target[k]:
            ok_mono = False
            print("     %s 的 %s 线从 %d 降到 %d" % (s["name"], k, prev_target[k], t))
        prev_target[k] = t
check(ok_mono, "同一维度的验收线不会倒退")

print("\n== 5. 题库 ==")
check(len(topics.ALL_TOPICS) >= 30, "题库 %d 题 >= 30" % len(topics.ALL_TOPICS))
first = topics.todays_topic(0)
check(first is not None and first.get("topic"), "每日一题能取到内容")
check(topics.todays_topic(len(topics.ALL_TOPICS))["id"] == first["id"], "取模越界能安全回绕")
bad_sec = [t["id"] for t in topics.ALL_TOPICS if t.get("seconds") not in (30, 45, 60, 90)]
check(not bad_sec, "时限都是 30/45/60/90%s" % ("" if not bad_sec else " —— 异常: %s" % bad_sec))

print("\n== 6. 素材库规模 ==")
check(len(passages.ALL_READ) >= 40, "朗读素材 %d >= 40" % len(passages.ALL_READ))
for key in ("twister", "broadcast", "morning"):
    n = len(passages.GROUPS[key]["items"])
    check(n >= 5, "分组 %s 有 %d 条 >= 5" % (key, n))

print()
if FAILS:
    print("失败 %d 项：" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("全部通过（%d 项检查）" % CHECKS[0])
