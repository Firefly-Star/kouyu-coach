# -*- coding: utf-8 -*-
"""云端 LLM 内容反馈：只问可验证的问题，要求引用原文。

为什么这样设计（重要）
----------------------
让 LLM 直接评价"这段讲得好不好"，得到的一定是：
「整体表达流畅，观点清晰，建议可以更具体、加个例子」——零信息量，
而且换个提示词能给你相反的评价。这是 LLM 评演讲最典型的失败模式。

所以这里**不问好不好**，只问三件能被原文验证的事：

1. 结构有没有兑现：说了「三点」实际讲了几点（content.py 的规则版先跑，
   规则判不出来时才问 LLM，规则能判的绝不浪费一次调用）
2. 有没有前后矛盾：要求指出**哪两句**矛盾，指不出就不算
3. 有没有没解释的术语/空转句：要求引用原句

并且强制模型输出结构化 JSON，每个结论必须带 quote（原文引用）。
没引用到原文的结论，本地直接丢弃——这是防止它编的最有效手段。

配置（环境变量，不写进仓库）
--------------------------
KOUYU_LLM_BASE   OpenAI 兼容的 base_url，如 https://api.deepseek.com/v1
KOUYU_LLM_KEY    API Key
KOUYU_LLM_MODEL  模型名，默认 deepseek-chat
"""
import json
import os
import urllib.request

DEFAULT_BASE = os.environ.get("KOUYU_LLM_BASE", "")
DEFAULT_KEY = os.environ.get("KOUYU_LLM_KEY", "")
DEFAULT_MODEL = os.environ.get("KOUYU_LLM_MODEL", "deepseek-chat")
TIMEOUT = 40

SYSTEM_PROMPT = """你是一个严格的口语训练教练。你的任务不是评价好坏，而是从转写文本里提取可验证的事实。

硬性规则：
1. 只输出 JSON，不要任何解释性文字、不要 markdown 代码块。
2. 每一个结论都必须带 quote 字段，quote 必须是你从原文**逐字复制**的片段。
   如果你找不到可以逐字复制的原文来支持某个结论，就不要输出那个结论。
3. 不要给建议、不要评价风格、不要说"可以更具体"这类空话。
4. 没有发现问题的项，就返回空数组。

输出结构：
{
  "structure": {"claimed": 整数或null, "delivered": 整数或null, "quote": "原文片段"},
  "contradictions": [{"a": "原句1", "b": "原句2", "why": "为什么矛盾"}],
  "unexplained": [{"term": "术语或空转词", "quote": "原句"}]
}"""

USER_TEMPLATE = """下面是一段%{mode}口语的转写文本，时长约 %{dur} 秒。

请按系统提示的规则提取事实。特别注意：
- 如果说话人说了"我想说三点"，检查他实际展开了几点
- contradictions 最多列 3 条，只列你确定矛盾的
- unexplained 最多列 5 条，指的是"说了但没解释的术语"或"没有实际信息的句子"

文本：
%{text}"""


def configured():
    return bool(DEFAULT_BASE and DEFAULT_KEY)


def status():
    return {
        "configured": configured(),
        "base": DEFAULT_BASE or None,
        # 永远不回显 key，只告诉前端"有/没有"
        "model": DEFAULT_MODEL if configured() else None,
    }


def _post(url, payload, key):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _strip_fence(s):
    """模型经常把 JSON 包在 ```json 里，剥掉。"""
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def review(text, duration_sec=None, mode="即兴"):
    """调 LLM 做结构审查。返回 {ok, structure, contradictions, unexplained, error}

    失败一律返回结构化错误，绝不抛异常——内容分析是可选增强，
    它挂了不能影响主报告。
    """
    if not configured():
        return {"ok": False, "error": "没有配置 LLM（KOUYU_LLM_BASE / KOUYU_LLM_KEY）"}
    if not (text or "").strip():
        return {"ok": False, "error": "没有可分析的文本"}

    payload = {
        "model": DEFAULT_MODEL,
        "temperature": 0,          # 要事实不要发挥
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE % {
                "mode": mode,
                "dur": int(duration_sec or 0),
                "text": text[:4000],   # 防止超长把上下文撑爆
            }},
        ],
    }
    try:
        base = DEFAULT_BASE.rstrip("/")
        resp = _post(base + "/chat/completions", payload, DEFAULT_KEY)
        raw = resp["choices"][0]["message"]["content"]
        data = json.loads(_strip_fence(raw))
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # ---- 关键：过滤掉引不出原文的结论 ----
    # 模型最常见的失败模式是编一个"你说了X"但原文根本没有。
    # 只要 quote 在原文里找不到，这条结论直接丢掉，宁缺勿滥。
    plain = (text or "").replace(" ", "")

    def quoted(s):
        q = ((s or {}).get("quote") or "").replace(" ", "")
        return bool(q) and q in plain

    out = {"ok": True, "dropped": 0}
    st = data.get("structure") or {}
    out["structure"] = st if quoted(st) else None
    if out["structure"] is None:
        out["dropped"] += 1

    contras = []
    for c in (data.get("contradictions") or [])[:3]:
        a = (c.get("a") or "").replace(" ", "")
        b = (c.get("b") or "").replace(" ", "")
        if a and b and a in plain and b in plain:
            contras.append(c)
        else:
            out["dropped"] += 1
    out["contradictions"] = contras

    unexp = []
    for u in (data.get("unexplained") or [])[:5]:
        if quoted(u):
            unexp.append(u)
        else:
            out["dropped"] += 1
    out["unexplained"] = unexp
    return out
