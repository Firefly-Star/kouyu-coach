# -*- coding: utf-8 -*-
"""本地 ASR：把录音转成文字，音频全程不出本机。

为什么用 faster-whisper
-----------------------
* 纯本地推理（CTranslate2），不需要联网、不需要 API Key
* base 模型约 140MB，中文可用；small 约 470MB 更准但更慢
* 首次运行会从 HuggingFace 下载模型到本地缓存，之后就离线了

懒加载与降级
------------
模型加载要几百毫秒到几秒，绝不放在服务启动时——用不到内容分析的人
不该为此多等。所以第一次调用才加载，之后复用。

没装 faster-whisper 时 `available()` 返回 False，
调用方据此跳过内容层，整个程序照常工作——**不能因为缺一个可选依赖就崩**。
"""
import os
import threading

_LOCK = threading.Lock()
_MODEL = None
_MODEL_SIZE = os.environ.get("KOUYU_WHISPER_MODEL", "base")
_STATUS = {"state": "idle", "error": None}
# 最近一次导入 faster_whisper 失败的原因。打包后排错全靠它，
# 不然 available() 只会告诉你 False，不告诉你为什么。
_IMPORT_ERROR = None


def available():
    """faster-whisper 装了吗？（只查包，不加载模型）

    导入失败的**原因要留住**：原来这里 except 一句 pass，
    结果打包后 available() 返回 False，但没人知道为什么——
    是没装？还是缺 dll？还是循环导入？排错时全靠猜。
    现在把异常信息存进 _IMPORT_ERROR，status() 会带出来。
    """
    global _IMPORT_ERROR
    try:
        import faster_whisper  # noqa: F401
        _IMPORT_ERROR = None
        return True
    except Exception as e:
        _IMPORT_ERROR = "%s: %s" % (type(e).__name__, e)
        return False


def status():
    """给前端看的状态：能不能用、当前用什么模型、有没有报过错。"""
    d = {
        "available": available(),
        "model": _MODEL_SIZE,
        "loaded": _MODEL is not None,
        "state": _STATUS["state"],
        "error": _STATUS["error"],
        "import_error": _IMPORT_ERROR,
    }
    return d


def _load():
    """懒加载模型。第一次调用会从 HF 下载（约 140MB，之后走缓存）。"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        if not available():
            raise RuntimeError("没有安装 faster-whisper")
        _STATUS["state"] = "loading"
        try:
            from faster_whisper import WhisperModel
            # cpu + int8：不挑机器，速度可接受。有 GPU 的用户可以自己改。
            _MODEL = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            _STATUS["state"] = "ready"
            _STATUS["error"] = None
        except Exception as e:
            _STATUS["state"] = "error"
            _STATUS["error"] = "%s: %s" % (type(e).__name__, e)
            raise
    return _MODEL


def transcribe(wav_path, language="zh"):
    """转写一个 wav 文件。

    返回 {ok, text, segments:[{start,end,text}], duration, error}

    segments 带时间戳，这样内容层的"哪个词前面卡住了"才能和
    音频层的卡壳点对上——这是把两层真正打通的关键。
    """
    if not available():
        return {"ok": False, "error": "没有安装 faster-whisper", "text": ""}
    if not os.path.isfile(wav_path):
        return {"ok": False, "error": "找不到音频文件", "text": ""}
    try:
        model = _load()
    except Exception as e:
        return {"ok": False, "error": "模型加载失败：%s" % e, "text": ""}

    try:
        segs, info = model.transcribe(
            wav_path,
            language=language,
            beam_size=1,              # 贪心解码：快，口语转写够用
            vad_filter=True,          # 先用 VAD 切掉静音，避免把停顿转成空白
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,   # 长录音里防"幻觉接龙"
        )
        out = []
        for s in segs:
            out.append({"start": round(float(s.start), 2),
                        "end": round(float(s.end), 2),
                        "text": (s.text or "").strip()})
        text = "".join(x["text"] for x in out).strip()
        return {
            "ok": True,
            "text": text,
            "segments": out,
            "duration": round(float(getattr(info, "duration", 0.0)), 2),
            "language": getattr(info, "language", language),
        }
    except Exception as e:
        return {"ok": False, "error": "转写失败：%s: %s" % (type(e).__name__, e), "text": ""}


def align_stalls(stalls, segments):
    """把音频层的卡壳点（秒）对应到转写文本里的词。

    这是内容层最有用的产出之一：比起"第 4.57 秒停了一下"，
    「在『但不值得』前面卡了 0.81 秒」能直接告诉你去改哪句话。

    segments 里每段都有起止时间；卡壳点落在某个 segment 之前/之中时，
    取该段开头的几个字作为"卡在什么前面"。
    """
    if not stalls or not segments:
        return stalls
    out = []
    for st in stalls:
        t = st.get("t")
        hit = None
        for seg in segments:
            if seg["start"] <= t <= seg["end"]:
                hit = ("段内", seg["text"])
                break
        if hit is None:
            # 落在两段之间：说明是在这里断的，取下一段开头
            nxt = [s for s in segments if s["start"] >= t]
            if nxt:
                hit = ("段间", nxt[0]["text"])
        item = dict(st)
        if hit:
            item["where_text"] = hit[0]
            item["next_text"] = hit[1][:16]
        out.append(item)
    return out
