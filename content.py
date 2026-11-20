# -*- coding: utf-8 -*-
"""内容层分析：把「说了什么」变成可量化的指标。

设计立场
--------
上一个讨论里我们分析过市面上晨读类工具的毛病：让 LLM 直接"点评内容"，
结果必然是「整体流畅、观点清晰，建议结论先行」这种零信息量的套话，
换个提示词评价还能翻面。

所以这个模块分两层，**优先级严格区分**：

第一层：硬指标（不需要 AI，纯统计，准确可复现）
  - 填充词密度（然后/就是/那个/嗯…）
  - 自我重复率（同一句说两遍）
  - 口头禅（高频三字以上片段）
  - 卡壳点对应的词（哪个词前面卡住了）
  - 有效信息密度（实词占比）
  这些数字直接进报告、能画趋势、能当验收条件。

第二层：LLM 结论（可选，且有严格约束）
  只问**可验证**的结构性问题，不问"好不好"：
  - 说了「三点」实际讲了几点？（可数）
  - 有没有结论？（有无）
  - 有没有明显的自相矛盾？（可指认）
  并且要求它**引用原文**，引不出原文就不许下结论。

  这样做是因为：让 LLM 评价"讲得好不好"是它最不擅长的事，
  但让它做"从文本里提取可验证事实"是它相对擅长的。

没有 ASR、没有 Key 的时候，第一层照样跑（除卡壳点对应词之外），
模块不会因为缺依赖而崩。
"""
import re
from collections import Counter

# ---------------------------------------------------------------- 填充词
# 分两类：无意义的语气词，和有实义但被滥用的连接词。
# 分开统计是因为改法不同：前者直接删，后者要换成停顿或过桥句。
FILLER_EMPTY = ["嗯", "呃", "额", "啊这", "那个", "就是说", "怎么讲呢", "这这这"]
FILLER_CONNECT = ["然后", "就是", "其实", "反正", "所以说", "所以说呢",
                  "或者说", "这一块", "的话"]

# 中文实词判断用的白名单太薄，改用"停用词表"反向排除：
# 剩下的都当实词。停用词只收高频虚词。
STOPWORDS = set(
    "的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "这 那 这个 那个 他 她 它 我们 你们 他们 但是 因为 所以 如果 虽然 而且 或者 "
    "而 与 及 以及 被 把 给 让 对 从 向 于 为 以 之 其 此 该 等 等等 什么 怎么 "
    "可以 应该 需要 能够 已经 还 又 再 才 更 最 非常 比较 一些 这种 那样 时候 "
    "自己 别人 大家 事情 问题 东西 方面 情况 地方 样子 意思 时候 部分 过程 "
    "一个 两个 三个 第一 第二 第三".split()
)


def _plain(text):
    """把文本压成只有中文/字母数字的连续串，便于做子串统计。"""
    return re.sub(r"[^\u4e00-\u9fff A-Za-z0-9]", "", text or "")


def count_fillers(text):
    """统计填充词。返回 {'empty': {...}, 'connect': {...}, 'total': n, 'per_min': x}

    只统计**独立出现**的，不算词内包含：
    比如"然后"在"然后我"里算，在"天然后来"这种跨词组合里不算。
    中文没有分词，退化处理：用前后字符不是同词类来近似，够用。
    """
    t = text or ""
    hit_empty = Counter()
    hit_conn = Counter()
    for w in FILLER_EMPTY:
        c = t.count(w)
        if c:
            hit_empty[w] = c
    for w in FILLER_CONNECT:
        c = t.count(w)
        if c:
            hit_conn[w] = c
    total = sum(hit_empty.values()) + sum(hit_conn.values())
    return {
        "empty": dict(hit_empty),
        "connect": dict(hit_conn),
        "total": total,
        "chars": len(_plain(t)),
    }


def repeat_phrases(text, min_len=6, max_len=24):
    """找自我重复：同一段字数≥min_len 的片段出现两次以上。

    抓的是"这句话我好像刚才说过"——口语里极常见，也是听感啰嗦的主因。
    返回按重复次数排序的列表。
    """
    s = _plain(text)
    if len(s) < min_len * 2:
        return []
    seen = Counter()
    # 以 min_len 为窗滑动，统计所有出现过 ≥2 次的子串，
    # 再合并"互为子串"的重复（否则一个长重复会刷出一堆短重复）
    for i in range(len(s) - min_len + 1):
        seen[s[i:i + min_len]] += 1
    dup = {k: v for k, v in seen.items() if v >= 2}
    out = []
    used = set()
    for frag in sorted(dup, key=lambda k: -dup[k] * len(k)):
        if any(frag in u for u in used):
            continue
        # 尽量向右扩展成最长的重复片段
        best = frag
        while len(best) < max_len:
            j = s.find(best)
            if j < 0 or j + len(best) >= len(s):
                break
            cand = s[j:j + len(best) + 1]
            if s.count(cand) >= 2:
                best = cand
            else:
                break
        if s.count(best) >= 2:
            used.add(best)
            out.append({"text": best, "count": s.count(best)})
    return out[:10]


def catchphrases(text, top=8):
    """找口头禅：高频的 3~4 字片段。

    和 repeat_phrases 的区别：这个是"短且高频"（说了十几遍的套话），
    那个是"长但不一定高频"（整句重复）。
    """
    s = _plain(text)
    c = Counter()
    for n in (3, 4):
        for i in range(len(s) - n + 1):
            frag = s[i:i + n]
            if all(ch in STOPWORDS for ch in frag):
                continue
            c[frag] += 1
    return [{"text": k, "count": v} for k, v in c.most_common(top) if v >= 3]


def content_words(text):
    """粗略的中文实词占比。

    没有分词，用停用词表反向排除：不在停用词表里的 2 字以上片段当实词候选。
    精度有限，但能稳定反映"这段是空话多还是有信息"这个趋势。
    """
    s = _plain(text).replace(" ", "")
    if not s:
        return {"chars": 0, "content_chars": 0, "ratio": 0.0}
    content = 0
    i = 0
    while i < len(s):
        if s[i] in STOPWORDS:
            i += 1
            continue
        # 尝试匹配 2 字词，落在停用词表外就算实词
        if i + 1 < len(s) and s[i:i + 2] not in STOPWORDS:
            content += 2
            i += 2
        else:
            i += 1
    return {"chars": len(s), "content_chars": content,
            "ratio": round(content / len(s), 3)}


# ---------------------------------------------------------------- 结构校验
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def check_structure(text):
    """可验证的结构问题：说了几点、实际讲了几个、有没有结论句。

    这些**不需要 AI**，规则就能判，而且判错了用户能一眼看出来。
    这是"内容反馈"里唯一靠得住的部分。
    """
    t = text or ""
    out = {"claimed": None, "delivered": None, "verdict": None}

    # 1) 有没有声称"我要说 N 点"
    m = re.search(r"(说|讲|有|分)\s*([一两二三四五六七八九十])\s*(点|条|个方面|件事|个问题)", t)
    if m:
        out["claimed"] = _CN_NUM.get(m.group(2))

    # 2) 实际有没有出现"第一/第二/第三"这类**列举**标记
    #
    # 这里很容易误判：真实录音里出现了「嘴巴里再讲第一句，脑子里还在找第二句」，
    # 旧正则匹配到「第一」「第二」就当成在列举，于是报「讲了 2 点」——
    # 但那是「第一句/第二句」，是在描述两个动作，不是分类列举。
    #
    # 所以必须要求标记后面紧跟分隔符或量词（第一，/ 第一点 / 第一条 / 第一、），
    # 且不能是「第一句、第一步、第一天」这类序数修饰。
    marks = []
    for num, val in _CN_NUM.items():
        # 第一， ／ 第一、 ／ 第一点 ／ 第一条 ／ 第一方面
        if re.search(r"第%s\s*[，,、：:]\s*" % num, t) \
                or re.search(r"第%s\s*(点|条|方面|件事|个问题)" % num, t) \
                or re.search(r"第%s\s*$" % num, t, re.M):
            marks.append(val)
    # 也认 "1." "2." 这种
    marks += [int(x) for x in re.findall(r"(?<!\d)([1-9])[.、](?!\d)", t)]
    out["delivered"] = len(set(marks)) if marks else None

    if out["claimed"] and out["delivered"] is not None:
        if out["delivered"] < out["claimed"]:
            out["verdict"] = "说了 %d 点，实际展开 %d 点" % (out["claimed"], out["delivered"])
        elif out["delivered"] > out["claimed"]:
            out["verdict"] = "说了 %d 点，实际讲了 %d 点（多了）" % (out["claimed"], out["delivered"])
        else:
            out["verdict"] = "说了 %d 点，讲满 %d 点" % (out["claimed"], out["delivered"])
    elif out["claimed"] and out["delivered"] is None:
        out["verdict"] = "说了 %d 点，但没听到「第一、第二」这类展开" % out["claimed"]
    return out


def analyze_text(text, duration_sec=None):
    """内容层的总入口。没有 ASR 时，text 为空也能安全返回。"""
    res = {
        "ok": bool((text or "").strip()),
        "text": (text or "").strip(),
        "chars": len(_plain(text or "")),
        "duration": round(duration_sec, 1) if duration_sec else None,
    }
    if not res["ok"]:
        return res

    fill = count_fillers(text)
    mins = max((duration_sec or 0) / 60.0, 1e-3) if duration_sec else None
    res["fillers"] = fill
    res["fillers"]["per_min"] = round(fill["total"] / mins, 1) if mins else None
    res["repeats"] = repeat_phrases(text)
    res["catchphrases"] = catchphrases(text)
    res["content"] = content_words(text)
    res["structure"] = check_structure(text)

    # 语速自洽性：转写出的字数 ÷ 音频时长，应当和音频分析出的整体语速接近。
    # 差得太多说明识别漏字或音频时长异常，这时候内容层结论要打折看。
    if mins and duration_sec:
        res["speech_rate"] = round(res["chars"] / mins / 60.0, 2)
    return res
