# -*- coding: utf-8 -*-
"""
中文口语表达分析引擎（零第三方依赖，仅需 numpy + 标准库）

分析维度：
  语速 / 流畅度（停顿、卡壳）/ 语调（F0 起伏、句末走向）/ 音量稳定 / 音节覆盖准确度
输入：16bit PCM WAV（任意采样率，自动重采样到 16k）+ 参考逐字稿
输出：dict（总分、各维度分、指标、停顿时间轴、分句表、改进建议）
"""
import io
import math
import re
import wave

import numpy as np

SR = 16000
WIN = int(0.032 * SR)
HOP = int(0.010 * SR)
PNG_PUNCT = "，,。.！!？?；;：:、…—【】（）()《》“”\"' \n\t\r"
SENT_END = "。！？!?；;…\n"
CLAUSE_END = "，,、：:"


# ---------------------------------------------------------------- 音频读取
def read_wav(data: bytes):
    """解析 WAV。损坏/截断/非 WAV 的字节一律抬 ValueError，
    由 analyze() 转成可读的错误信息——不能让它抛 EOFError 变成 500。"""
    if not data or len(data) < 44:
        raise ValueError("音频太短（%d 字节），可能没录上声音" % len(data or b""))
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("不是有效的 WAV 音频（浏览器录音失败？请重试）")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (EOFError, wave.Error, OSError) as e:
        raise ValueError("音频文件不完整或已损坏（%s）" % type(e).__name__)
    if not raw:
        raise ValueError("音频里没有任何采样数据")
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype="<u1").astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("不支持的位深 %d bit" % (sw * 8))
    if nch < 1:
        raise ValueError("声道数异常")
    if nch > 1:
        usable = len(x) - len(x) % nch
        x = x[:usable].reshape(-1, nch).mean(axis=1)
    if sr != SR:
        tgt = int(round(len(x) * SR / float(sr)))
        x = np.interp(np.linspace(0, len(x) - 1, tgt, dtype=np.float64),
                      np.arange(len(x), dtype=np.float64), x).astype(np.float32)
    # 去直流
    if len(x):
        x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0:
        x = x / max(peak, 0.999) * min(peak, 0.999) if peak > 0.999 else x
    return x


# ---------------------------------------------------------------- 工具函数
def _filt(x, lo=300.0, hi=3400.0):
    if len(x) < 64:
        return x
    n = 1 << int(math.ceil(math.log2(len(x))))
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, n)[: len(x)].astype(np.float32)


def _smooth(v, k):
    if k <= 1 or len(v) < k:
        return v
    kern = np.ones(k, dtype=np.float32) / k
    return np.convolve(v, kern, mode="same")


def _frames(x, size=WIN, hop=HOP):
    if len(x) < size:
        x = np.pad(x, (0, size - len(x)))
    n = 1 + (len(x) - size) // hop
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def _db(v):
    return 20.0 * np.log10(np.maximum(v, 1e-7))


def pinyin_syllables(text):
    """每个汉字记 1 个音节；连续英文/数字按词处理；标点不计。返回 [(char, is_syl)]"""
    out = []
    for ch in text:
        if ch in PNG_PUNCT:
            out.append((ch, 0))
        elif ord(ch) > 0x2E80:  # CJK 及扩展
            out.append((ch, 1))
        elif ch.isalnum():
            out.append((ch, 1))
        else:
            out.append((ch, 0))
    return out


def split_clauses(text):
    """按句读切分为分句，返回 [(clause_text, expected_syllables, is_sentence_end)]"""
    res, buf = [], ""
    for ch in text:
        if ch in PNG_PUNCT and ch not in "\n\t\r ":
            if buf.strip():
                res.append((buf, sum(1 for c, s in pinyin_syllables(buf) if s), ch in SENT_END))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        res.append((buf, sum(1 for c, s in pinyin_syllables(buf) if s), True))
    return res


# ---------------------------------------------------------------- 语音活动检测
def vad(x):
    """双门限迟滞 VAD。

    单门限会遇到一个真实问题：录音电平低时（手机/MIC 阵列常见 -25dBFS），
    门限正好落在音节尾部能量上，词尾被切碎 → 假停顿满天飞。
    所以先用一个高门限找“确定在说话”的核心，再向外扩张到低门限，
    词尾、弱声母都会被并回来，而真正的静音（低于低门限）仍然留作停顿。
    """
    if len(x) < WIN * 4:
        return np.zeros(1, dtype=bool), np.zeros(1)
    fr = _frames(x)
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    db = _db(_smooth(rms, 5))            # ~50ms，抹掉音节内起伏但保留词边界
    noise = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 90))
    rng = loud - noise
    if rng < 7.0:                        # 近乎静音/纯噪声
        return np.zeros(len(db), dtype=bool), db
    thr_hi = noise + 0.45 * rng
    thr_lo = noise + 0.18 * rng

    core = db > thr_hi
    if not np.any(core):                 # 退化保护
        core = db > thr_lo
    mask = core.copy()
    # 从核心向两侧扩张，直到能量掉到低门限以下
    lo = db > thr_lo
    n = len(db)
    idx = np.where(core)[0]
    for start, stop in segments(core):
        i = int(start * SR / HOP)
        j = int(stop * SR / HOP)
        i = max(0, i)
        j = min(n, j)
        while i > 0 and lo[i - 1] and (core[i - 1] == False):
            i -= 1
        while j < n and lo[j] and (core[j] == False):
            j += 1
        mask[i:j] = True
    # 补掉词内小空隙（<120ms），再丢掉过短碎片（<60ms）
    mask = _close(mask, 12)
    for a, b in list(segments(mask)):
        if b - a < 0.06:
            mask[int(a * SR / HOP):max(int(a * SR / HOP) + 1, int(b * SR / HOP))] = False
    return mask, db


def _close(mask, max_gap):
    m = mask.copy()
    i = 0
    n = len(m)
    while i < n:
        if not m[i]:
            j = i
            while j < n and not m[j]:
                j += 1
            if i > 0 and j < n and (j - i) <= max_gap:
                m[i:j] = True
            i = j
        else:
            i += 1
    return m


def segments(mask):
    segs, n, i = [], len(mask), 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            segs.append((i * HOP / SR, j * HOP / SR))
            i = j
        else:
            i += 1
    return segs


def trim_artifacts(segs):
    """去掉开头/结尾的“咔哒”声：很短且与后面隔着长静音的碎片。

    鼠标/麦克风在按下瞬间常留下 100ms 的能量爆点，它会被当成一句话，
    让第一处句读锚点整体跑偏。只在边界处清理，中间的短音节不动。
    """
    segs = list(segs)
    while len(segs) > 1 and (segs[0][1] - segs[0][0]) < 0.15 and (segs[1][0] - segs[0][1]) > 0.30:
        segs.pop(0)
    while len(segs) > 1 and (segs[-1][1] - segs[-1][0]) < 0.15 and (segs[-1][0] - segs[-2][1]) > 0.30:
        segs.pop()
    return segs


# ---------------------------------------------------------------- 音节检测
def _peaks_with_prominence(env, min_dist, min_prom, min_abs):
    """能量包络上的峰检测：只有“突出度”够高的峰才算一个音节核。"""
    n = len(env)
    cand = []
    for k in range(1, n - 1):
        v = env[k]
        if v < min_abs:
            continue
        if v >= env[k - 1] and v > env[k + 1]:
            i, lmin = k - 1, v
            while i >= 0 and env[i] <= v:
                if env[i] < lmin:
                    lmin = env[i]
                i -= 1
            j, rmin = k + 1, v
            while j < n and env[j] <= v:
                if env[j] < rmin:
                    rmin = env[j]
                j += 1
            prom = v - max(lmin, rmin)
            if prom >= min_prom:
                cand.append((k, float(prom)))
    # 过近的峰只留突出度大的
    out = []
    for k, prom in cand:
        if out and (k - out[-1][0]) < min_dist:
            if prom > out[-1][1]:
                out[-1] = (k, prom)
        else:
            out.append((k, prom))
    return [k for k, _ in out]


def detect_syllables(x, mask):
    if not np.any(mask):
        return np.array([], dtype=np.float32)
    sig = _filt(x)
    fr = _frames(sig, int(0.040 * SR), HOP)
    env = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    env = _smooth(env, 5)          # ~50ms 平滑，抹掉音节内的次级起伏
    m = mask[: len(env)]
    if len(m) < len(env):
        m = np.pad(m, (0, len(env) - len(m)))
    env_m = env.copy()
    env_m[~m] = 0.0
    peak = float(np.max(env_m)) if len(env_m) else 0.0
    if peak <= 0:
        return np.array([], dtype=np.float32)
    env_m = env_m / peak
    # 最短音节间隔 95ms（≈10 字/秒上限）：正常说话 4~6 字/秒，留足余量
    min_dist = int(0.095 * SR / HOP)
    times = _peaks_with_prominence(env_m, min_dist, 0.03, 0.015)
    return np.array(times, dtype=np.float32) * HOP / SR


# ---------------------------------------------------------------- 基频
def f0_track(x, mask):
    size, hop = int(0.040 * SR), int(0.010 * SR)
    fr = _frames(x, size, hop)
    n = len(fr)
    f0 = np.zeros(n, dtype=np.float32)
    voiced = np.zeros(n, dtype=bool)
    lo, hi = int(SR / 400.0), int(SR / 70.0)
    w = np.hanning(size).astype(np.float32)
    for i in range(n):
        if i >= len(mask) or not mask[i]:
            continue
        f = fr[i] * w
        e = float(np.sqrt(np.mean(f ** 2)))
        if e < 1e-4:
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[size - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lo:hi]
        if len(seg) < 3:
            continue
        k = int(np.argmax(seg)) + lo
        if ac[k] < 0.42:
            continue
        # 抛物线插值
        if 0 < k < len(ac) - 1:
            a, b, c = ac[k - 1], ac[k], ac[k + 1]
            d = (a - c) / (2.0 * (a - 2 * b + c)) if (a - 2 * b + c) != 0 else 0.0
            k = k + float(np.clip(d, -1, 1))
        f0[i] = SR / k
        voiced[i] = True
    # 中值平滑
    if np.any(voiced):
        v = f0[voiced]
        med = np.median(v)
        keep = (v > med * 0.5) & (v < med * 1.8)
        f0[voiced] = np.where(keep, v, med)
    return f0, voiced


# ---------------------------------------------------------------- 指标计算
def _semi(v, ref):
    return 12.0 * math.log(max(v, 1e-6) / max(ref, 1e-6), 2)


def analyze(wav_bytes, text=None, target_rate=4.2):
    has_text = bool(text and text.strip())
    # 坏音频一律走“结构化错误”而不是抛异常：前端只认 {ok:false,error}，
    # 抛出去会变成 500 + traceback，用户只看到“分析失败：EOFError”。
    try:
        x = read_wav(wav_bytes)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                    # 兜底：解码库抛什么都别让它冒到 HTTP 层
        return {"ok": False, "error": "音频解析失败（%s）" % type(e).__name__}
    if len(x) < SR * 0.3:
        return {"ok": False, "error": "录音太短（不到 0.3 秒），请按素材正常念一句再停。"}
    total_dur = len(x) / float(SR)
    mask, db = vad(x)
    segs = trim_artifacts(segments(mask))
    if not segs:
        return {"ok": False, "error": "没有检测到有效的语音，请确认麦克风录音正常、音量不过低。"}

    first, last = segs[0][0], segs[-1][1]
    speech_time = sum(b - a for a, b in segs)
    span = max(last - first, 0.001)

    # 停顿（有声段之间的静音）
    pauses = []
    for k in range(1, len(segs)):
        d = segs[k][0] - segs[k - 1][1]
        if d >= 0.12:
            pauses.append({"start": round(segs[k - 1][1], 2), "end": round(segs[k][0], 2),
                           "dur": round(d, 2)})
    long_pauses = [p for p in pauses if p["dur"] >= 0.60]
    hesitations = [p for p in pauses if 0.30 <= p["dur"] < 0.60]

    syl_t = detect_syllables(x, mask)
    n_syl = int(len(syl_t))
    expected = 0
    if has_text:
        expected = sum(1 for c, s in pinyin_syllables(text) if s)
    # 无稿模式下“期望音节数”没有意义，硬兜成 1 会让覆盖率显示成 222.0 这种鬼数字。
    # 内部仍需要一个非零值做除法保护，但对外(null) 才是诚实的说法。
    expected_report = expected if has_text else None
    expected = max(expected, 1)

    # 两个语速口径，别混用：
    #   整体语速（speech rate）  = 音节数 / 开口到收尾的总时长，含停顿 —— 人耳听到的就是它
    #   吐字速率（articulation）= 音节数 / 纯发声时长，扣除停顿 —— 只反映“嘴动得快不快”
    # 打分以整体语速为准。结巴/卡壳恰恰是停顿造成的，把停顿排除在语速之外，
    # 会得出“停顿占了 1/3 时间、语速却满分”这种荒谬结论。
    rate_artic = n_syl / max(speech_time, 0.5)
    rate = n_syl / max(span, 0.5)          # 主口径：整体语速
    # 停顿吃掉的时间占比，用来解释两个口径的差距
    pause_time = max(span - speech_time, 0.0)
    pause_ratio = pause_time / max(span, 1e-6)

    # 音节间隔规律性（抖动 = 卡壳感的客观来源之一）
    if n_syl >= 4:
        d = np.diff(syl_t)
        d = d[d < 1.4]
        if len(d) >= 3:
            med = float(np.median(d))
            jitter = float(1.4826 * np.median(np.abs(d - med)) / max(med, 1e-3))
            mean_isi = float(np.mean(d))
        else:
            jitter, mean_isi = 0.0, 0.0
    else:
        jitter, mean_isi = 0.0, 0.0

    # 语调
    f0, voiced = f0_track(x, mask)
    v = f0[voiced]
    v = v[(v > 60) & (v < 420)]
    if len(v) >= 20:
        f0_med = float(np.median(v))
        p10, p90 = float(np.percentile(v, 10)), float(np.percentile(v, 90))
        f0_range = abs(_semi(p90, p10))
        st = np.array([_semi(float(a), f0_med) for a in v])
        f0_std = float(np.std(st))
        # 句末走向：每段有声区尾部 0.25s 的斜率
        ends = []
        for a, b in segs:
            ia, ib = int(max(a, b - 0.25) / 0.01), int(b / 0.01)
            ia, ib = max(ia, 0), min(ib, len(f0))
            seg_f = f0[ia:ib]
            seg_v = voiced[ia:ib]
            if seg_v.sum() >= 8:
                tt = np.arange(len(seg_f))[seg_v]
                yy = np.array([_semi(float(q), f0_med) for q in seg_f[seg_v]])
                if len(tt) >= 5:
                    slope = float(np.polyfit(tt * 0.01, yy, 1)[0])
                    ends.append(slope)
        end_slope_mean = float(np.mean(ends)) if ends else 0.0
        flat_ratio = float(np.mean(np.abs(ends) < 0.6)) if ends else 1.0
    else:
        f0_med, f0_range, f0_std, end_slope_mean, flat_ratio = 180.0, 0.0, 0.0, 0.0, 1.0

    # 音量稳定
    rms_all = np.sqrt(np.mean(_frames(x) ** 2, axis=1) + 1e-12)
    rms_sp = rms_all[mask[: len(rms_all)]] if np.any(mask) else rms_all
    vol_std = float(np.std(_db(_smooth(rms_sp, 5)))) if len(rms_sp) > 4 else 0.0
    peak_db = float(_db(np.max(np.abs(x)))) if len(x) else -99.0
    clipping = float(np.mean(np.abs(x) > 0.985)) if len(x) else 0.0

    # ---------------- 训练相关指标（气息 / 起音 / 句中最大句长）
    # 起音（gentle onset）：不是从 VAD 门限算 dB 爬升（那是被门限决定的，全是 45dB），
    # 而是量“振幅从 15% 爬到 85% 用了多少毫秒”。
    #   <18ms  = 硬起音：气息还没到，声带先撞上，听起来像“呃！”一下顶出来
    #   30~80ms = 轻柔起音：气先到、声后到，开头顺，不容易卡
    env_lin = np.sqrt(np.mean(_frames(x) ** 2, axis=1) + 1e-12)
    rise_ms = []
    for a, b in segs:
        i0 = int(a * SR / HOP)
        i1 = min(len(env_lin), i0 + int(0.16 * SR / HOP) + 1)
        if i1 - i0 < 5:
            continue
        seg = env_lin[i0:i1]
        pk = float(np.max(seg))
        if pk <= 0:
            continue
        seg = seg / pk
        lo_th, hi_th = 0.15, 0.85
        # 峰值必须落在观察窗内，否则这一段开头被截断了，不能用来判起音
        if float(np.argmax(seg)) >= len(seg) - 2:
            continue
        i_lo = int(np.argmax(seg >= lo_th))
        i_hi = int(np.argmax(seg >= hi_th))
        if i_hi <= i_lo:
            continue
        rise_ms.append((i_hi - i_lo) * HOP * 1000.0 / SR)
    onset_hard_ratio = (sum(1 for r in rise_ms if r < 18.0) / float(len(rise_ms))) if rise_ms else 0.0
    onset_rise = float(np.median(rise_ms)) if rise_ms else 0.0

    # 气息续航：单个连续发声段最长能撑多久（越长说明气息越稳，不会一句话换三次气）
    longest_seg = max((b - a for a, b in segs), default=0.0)
    seg_durs = [b - a for a, b in segs]
    median_seg = float(np.median(seg_durs)) if seg_durs else 0.0

    # ---------------- 分句对齐（在时间轴上按期望字数比例切，并吸附到真实停顿）
    # 用时间而不是“第几个音节”来切：音节检测器在连读处会合并音节，
    # 按索引切会让后面每一句整体错位（甚至出现某句“一个音节都没有”）。
    clauses = split_clauses(text) if has_text else []
    clause_rows = []
    if clauses:
        exp = np.array([max(c[1], 1) for c in clauses], dtype=float)
        frac = np.concatenate([[0.0], np.cumsum(exp)]) / exp.sum()   # 0..1 累计比例
        # 自左向右分配停顿：每个边界只能用一个“比上一个边界更晚”的停顿中点，
        # 且必须比它自己的比例估计更近；这样不会两条边界抢同一个停顿。
        mids = sorted((p["start"] + p["end"]) / 2.0 for p in pauses)
        window = (last - first) / max(len(clauses) - 1, 1)
        cuts = [first]
        used = 0
        for i in range(1, len(clauses)):
            guess = first + (last - first) * float(frac[i])
            cand, cand_d = None, None
            for k in range(used, len(mids)):
                d = abs(mids[k] - guess)
                if cand_d is None or d < cand_d:
                    cand, cand_d, used = mids[k], d, k + 1
                if mids[k] > guess + window:   # 越走越远，后面不用看了
                    break
            if cand is not None and cand_d is not None and cand_d <= 0.55 * window \
                    and cand > cuts[-1]:
                cuts.append(cand)
            else:
                cuts.append(max(guess, cuts[-1] + 0.06))
        cuts.append(last)
        for i in range(1, len(cuts)):
            if cuts[i] < cuts[i - 1] + 0.06:
                cuts[i] = cuts[i - 1] + 0.06
        cuts[-1] = max(cuts[-1], cuts[-2] + 0.06)
        for i, (ctext, esyl, is_end) in enumerate(clauses):
            t0, t1 = cuts[i], cuts[i + 1]
            sub = syl_t[(syl_t >= t0) & (syl_t < t1)] if n_syl else np.array([])
            det = len(sub)
            dur = max(t1 - t0, 0.05)
            inside = [p for p in pauses if t0 - 0.10 <= p["start"] < t1]
            clause_rows.append({
                "text": ctext,
                "expected": int(esyl),
                "detected": det,
                "start": round(t0, 2),
                "end": round(t1, 2),
                "dur": round(dur, 2),
                "rate": round(det / dur, 2) if det else 0.0,
                "pauses": len(inside),
                "long_pauses": len([p for p in inside if p["dur"] >= 0.60]),
                "ratio": round(det / max(esyl, 1), 2),
                "sentence_end": bool(is_end),
            })

    # ---------------- 计分
    def clamp(v, a=0.0, b=100.0):
        return float(max(a, min(b, v)))

    # 语速分：用**整体语速**（含停顿）。舒适区 3.4~4.8 字/秒。
    # 低于 3.0 说明停顿太多或吐字太慢；高于 5.2 别人跟不上。
    if rate < 3.4:
        rate_score = clamp(100 - (3.4 - rate) * 30)
    elif rate > 4.8:
        rate_score = clamp(100 - (rate - 4.8) * 28)
    else:
        rate_score = 100.0

    # 吐字速率和整体语速差太多 = 停顿在拖后腿，单独提示（不再计入语速分，避免重复扣分）
    rate_gap = rate_artic - rate

    speech_ratio = speech_time / max(span, 0.001)
    dur_min = max(span / 60.0, 0.05)
    long_per_min = len(long_pauses) / dur_min
    hes_per_min = len(hesitations) / dur_min

    # 流畅分：整体语速已经把“停顿总时长”算进去了，这里**只**衡量
    # 停顿的质量（长停顿、犹豫停顿、节奏抖动），避免同一件事扣两次分。
    #
    # 校准依据：拿 19 条真实录音回归后发现，原先每长停顿扣 7 分太狠——
    # 每分钟 6~7 次长停顿（真人正常背诵/条理表达也会有）就被扣到 20 分，
    # 导致「流畅」这一维度 0% 满分、中位数 29，训练计划第二阶段的
    # flow ≥ 65 永远够不到。真正该被压到 20~30 分的是每分钟 15 次以上。
    # 现在的曲线：3 次/分 ≈ 82 分，7 次/分 ≈ 58 分，12 次/分 ≈ 28 分。
    # 短录音（<6 秒）里次数会被放大（10 秒 1 次 = 6 次/分），按长度做置信度缩放。
    conf = min(1.0, max(0.0, (span - 6.0) / 12.0))       # 6s→0，18s→1
    scale = 1.0 if span >= 12 else 0.55 + 0.45 * conf

    # 长停顿：1 次/分以内不扣（正常句读），之后线性加重
    long_term = min(42.0, max(0.0, long_per_min - 1.0) * 5.0) * scale
    # 犹豫停顿：8 次/分以内不扣（正常换气与思考），之后加重
    hes_term = min(20.0, max(0.0, hes_per_min - 8.0) * 1.3) * scale
    # 节奏抖动：0.30 以内不扣（正常语流），0.65 以上扣满
    jitter_term = min(22.0, max(0.0, jitter - 0.30) * 63.0)

    fluency = 100.0 - long_term - hes_term - jitter_term
    # 说话占比过低（<0.6）时再轻罚：整段几乎没在说，已经不是“停顿质量”问题
    fluency -= max(0.0, 0.60 - speech_ratio) * 80
    fluency = clamp(fluency)

    cheap = 100.0
    if f0_range < 3.0:
        cheap -= (3.0 - f0_range) * 13
    if f0_range > 11.0:
        cheap -= (f0_range - 11.0) * 8
    if f0_std < 1.2:
        cheap -= (1.2 - f0_std) * 18
    if f0_std > 4.5:
        cheap -= (f0_std - 4.5) * 9
    cheap -= flat_ratio * 30
    intonation = clamp(cheap)

    ratio = n_syl / float(expected)
    if has_text:
        # 检测器在连读处天然会合并掉约 15~30% 的音节（不同人、不同麦都不同），
        # 这是设备偏差而不是你的问题。用“本段的基线检出率”做归一化，
        # 只惩罚相对偏差：某句比本段平均糊得更厉害才扣分。
        base = float(np.median([c["ratio"] for c in clause_rows if c["expected"] >= 3])) \
            if any(c["expected"] >= 3 for c in clause_rows) else ratio
        base = base if 0.3 <= base <= 1.6 else ratio
        norm = ratio / max(base, 1e-3)
        acc = 100.0
        acc -= abs(1.0 - norm) * 85
        weak = [c for c in clause_rows if c["expected"] >= 3 and c["ratio"] < 0.55 * base]
        acc -= len(weak) * 7
        if peak_db < -28:
            acc -= 10
        accuracy = clamp(acc)
        acc_label = "读音准确度"
    else:
        # 无文本模式：用“音节是否都清晰落地”替代覆盖率——含停顿的语速 + 音节密度
        weak = []
        target_span = n_syl / max(target_rate, 0.5)
        acc = 100.0
        acc -= abs(1.0 - target_span / max(span, 0.5)) * 70
        if peak_db < -28:
            acc -= 12
        if clipping > 0.02:
            acc -= 10
        accuracy = clamp(acc)
        acc_label = "语流完整度"

    volume = clamp(100 - max(0.0, vol_std - 5.0) * 7 - (12 if clipping > 0.02 else 0))

    # ---- 气息与起音（训练维度，不计入总分权重，单独给出）
    # 这两个维度最容易“躺着满分”，必须用能拉开差距的口径：
    #
    # 起音：光看硬起音比例会饱和（真人说话大多 0~10%）。
    # 真正有区分度的是**起音爬升的中位毫秒数**——
    # 最舒服的区间在 45~75ms（气先到、声后到）；
    # <20ms 是撞出来，>120ms 是发虚、拖泥带水。
    # 注意：rise 是按 10ms 帧量出来的，只有几十个离散值，所以曲线必须连续，
    # 否则 30~60ms 会全部顶在 100 分，等于没有区分度。
    if onset_rise <= 0:
        rise_part = 0.0
    elif onset_rise < 45.0:
        rise_part = 0.55 + 0.45 * max(0.0, (onset_rise - 15.0) / 30.0)   # 15ms→0.55，45ms→1.0
    elif onset_rise <= 75.0:
        rise_part = 1.0
    else:
        rise_part = max(0.0, 1.0 - (onset_rise - 75.0) / 75.0)           # 150ms→0
    rise_part = max(0.0, min(1.0, rise_part))
    # 硬起音比例：一票否决式的补充，硬起音越多越差
    hard_part = max(0.0, 1.0 - max(0.0, onset_hard_ratio - 0.05) * 4.0)
    onset_score = clamp(100.0 * (0.55 * rise_part + 0.45 * hard_part))

    # 气息：一口气能撑多久，但要有“够用线”而不是线性拉满——
    # 超过 3.5 秒不再加分（那是长段朗读，不是气息好坏的证据）。
    if not segs:
        breath_score = 0.0
    else:
        span_part = 1.0 if longest_seg >= 3.5 else longest_seg / 3.5
        med_part = min(1.0, median_seg / 1.6)                 # 太碎＝一句换好几次气
        ratio_part = min(1.0, max(0.0, (speech_ratio - 0.45) / 0.35))
        breath_score = clamp(100.0 * (0.45 * span_part + 0.30 * med_part + 0.25 * ratio_part))

    overall = (0.28 * fluency + 0.24 * accuracy + 0.20 * rate_score
               + 0.18 * intonation + 0.10 * volume)

    # ---------------- 卡壳点（句内长停顿）
    stalls = []
    for p in pauses:
        if p["dur"] >= 0.45:
            seg_idx = 0
            for i, c in enumerate(clause_rows):
                if c["start"] - 0.3 <= p["start"] <= c["end"] + 0.3:
                    seg_idx = i
                    break
            # 无稿（即兴）模式没有分句表，就没有“句内/句间”可言：
            # 以前这里拿 p["start"] 和 0 比，恒定判成“分句内部”，
            # 于是同一行里位置写“分句内部”、分句却写“句间”，自相矛盾。
            if clause_rows:
                ctx = clause_rows[seg_idx]["text"]
                where = "分句之间" if abs(p["start"] - clause_rows[seg_idx]["end"]) < 0.4 else "分句内部"
            else:
                ctx = "即兴（无稿）"
                where = "语流中"
            stalls.append({"t": p["start"], "dur": p["dur"],
                           "where": where, "clause": ctx[:18]})

    advice = _advice(rate, rate_score, speech_ratio, long_per_min, hes_per_min, jitter,
                     f0_range, f0_std, flat_ratio, ratio, weak, vol_std, peak_db, stalls,
                     has_text=has_text, rate_artic=rate_artic, pause_ratio=pause_ratio)

    return {
        "ok": True,
        "has_text": has_text,
        "score": round(overall, 1),
        "subscores": {"流畅度": round(fluency, 1), acc_label: round(accuracy, 1),
                      "语速": round(rate_score, 1), "语调": round(intonation, 1),
                      "音量稳定": round(volume, 1)},
        # 训练四维：计划里每个阶段盯的就是这里的一到两项
        "dims": {
            "flow": round(fluency, 1),          # 流畅：停顿、卡壳、节奏抖动
            "prosody": round(intonation, 1),    # 语调：起伏、句末收束
            "breath": round(breath_score, 1),   # 气息：一口气能说多久
            "onset": round(onset_score, 1),     # 起音：是不是撞出来的开头
            "pace": round(rate_score, 1),       # 语速
            "clarity": round(accuracy, 1),      # 字音是否落地
        },
        # 这条录音的成绩可信吗？太短的片段算出来的维度会虚高，
        # 进度/阶段判定只采信 reliable=True 的记录。
        "reliable": bool(span >= 12.0 and n_syl >= 25),
        "confidence": round(min(1.0, (span / 15.0) * (min(n_syl, 40) / 40.0)), 3),
        "metrics": {
            "录音总时长": round(total_dur, 2),
            "有效说话时长": round(speech_time, 2),
            "停顿总时长": round(pause_time, 2),
            "说话占比": round(speech_ratio, 3),
            "停顿占比": round(pause_ratio, 3),
            "检测音节数": n_syl,
            "期望音节数": expected_report,
            "音节覆盖率": round(ratio, 3) if has_text else None,
            "整体语速_字每秒": round(rate, 2),
            "吐字速率_字每秒": round(rate_artic, 2),
            "口径差_字每秒": round(rate_gap, 2),
            "停顿次数": len(pauses),
            "长停顿次数_0.6s以上": len(long_pauses),
            "犹豫停顿_0.3至0.6s": len(hesitations),
            "每分钟长停顿": round(long_per_min, 2),
            "音节间隔抖动": round(jitter, 3),
            "平均音节间隔": round(mean_isi, 3),
            "基频中值Hz": round(f0_med, 1),
            "基频起伏_半音": round(f0_range, 2),
            "基频标准差_半音": round(f0_std, 2),
            "句末平拖占比": round(flat_ratio, 3),
            "句末音高斜率": round(end_slope_mean, 2),
            "音量波动dB": round(vol_std, 2),
            "峰值电平dBFS": round(peak_db, 1),
            "削波比例": round(clipping, 4),
            "最长连续发声s": round(longest_seg, 2),
            "单段中位时长s": round(median_seg, 2),
            "硬起音比例": round(onset_hard_ratio, 3),
            "起音爬升dB": round(onset_rise, 1),
        },
        "segments": [[round(a, 2), round(b, 2)] for a, b in segs],
        "pauses": pauses,
        "stalls": stalls[:12],
        "clauses": clause_rows,
        "syllables": [round(float(t), 2) for t in syl_t][:4000],
        "advice": advice,
    }


def _advice(rate, rate_score, speech_ratio, long_pm, hes_pm, jitter,
            f0_range, f0_std, flat_ratio, ratio, weak, vol_std, peak_db, stalls, has_text=True,
            rate_artic=None, pause_ratio=0.0):
    out = []
    if rate < 3.0:
        out.append("整体语速只有 %.1f 字/秒（含停顿）：听众会觉得你在“挤”。先降难度，用节拍器两字一拍把节奏钉住，稳定后再提速。" % rate)
    elif rate < 3.4:
        out.append("整体语速 %.1f 字/秒（含停顿），略慢。用节拍器把每句切成 2~3 字一组，一组一拍，稳定后再慢慢加速。" % rate)
    elif rate > 5.2:
        out.append("整体语速 %.1f 字/秒（含停顿）：别人跟不上。在逗号处强制停 0.3 秒，让结尾的字咬完整。" % rate)
    elif rate > 4.8:
        out.append("整体语速 %.1f 字/秒（含停顿），略快。逗号处多停半拍，把节奏压回来。" % rate)
    else:
        out.append("整体语速 %.1f 字/秒（含停顿），落在舒适区，保持住这个节奏。" % rate)

    # 两个口径差距大 = 停顿在拖后腿，而不是嘴慢
    if rate_artic is not None and rate_artic - rate >= 0.8:
        out.append(
            "注意口径差：吐字速率 %.1f 字/秒，但整体只有 %.1f 字/秒——"
            "也就是说停顿吃掉了 %.0f%% 的时间。你的嘴不算慢，问题在停。"
            "把句内长停顿压到 0.4 秒以内，整体语速会自动追上来。"
            % (rate_artic, rate, pause_ratio * 100))
    elif rate_artic is not None and rate < 3.4 and rate_artic < 3.4:
        out.append("吐字速率也只有 %.1f 字/秒：不是停顿的问题，是每个字本身推得慢。用节拍器逐级加速。" % rate_artic)

    if long_pm >= 3:
        out.append("每分钟有 %.1f 次 0.6 秒以上的长停顿，这是“卡壳感”的主要来源。挑出报告里标出的卡壳点，把那句单独念 10 遍到不打结再说。" % long_pm)
    if hes_pm >= 12:
        out.append("0.3~0.6 秒的犹豫停顿偏多（每分钟 %.0f 次）：多半是在想下一个词。先把要说的内容写成 3 个关键词，再开口。" % hes_pm)
    if jitter > 0.45:
        out.append("音节间隔忽长忽短（抖动 %.2f）：说明节奏不稳。用“匀速念数字 1—2—3—4”热身 1 分钟再读正文。" % jitter)
    if f0_range < 3.0 or flat_ratio > 0.6:
        out.append("语调偏平（起伏仅 %.1f 个半音，句末平拖占 %.0f%%）：每句重音字往上抬一点，陈述句末尾往下压，问句末尾抬起来。" % (f0_range, flat_ratio * 100))
    if f0_range > 11.0:
        out.append("音高起伏过大（%.1f 个半音）：大起大落会显得紧张，把夸张的升调收回一半。" % f0_range)
    if has_text:
        if ratio < 0.55:
            out.append("检测到的音节只有期望的 %.0f%%：低于本工具的正常检出水平，大概率有整句漏读、含糊带过或录音过轻。对着文本逐字点读一遍，确认每个字都出声。" % (ratio * 100))
        elif ratio > 1.35:
            out.append("检测音节多于文本（%.0f%%）：可能夹了“嗯、呃、那个”之类填充词，或者同一个字重复了。回听录音找出来。" % (ratio * 100))
        if weak:
            sample = "」「".join(w["text"][:14] for w in weak[:3])
            out.append("这几句比这一段里其他句子明显更糊、更赶：「%s」，单独重录这 %d 句。" % (sample, len(weak)))
    else:
        out.append("即兴模式不校验逐字稿，重点看语速、停顿和语调三项；下一遍试着把内容压成三个关键词再开口。")
    if vol_std > 8:
        out.append("音量波动 %.1f dB：句尾明显泄气。练习“句尾不掉音”，最后一个字也要保持音量。" % vol_std)
    if peak_db < -28:
        out.append("录音电平偏低（峰值 %.0f dBFS）：离麦克风近一点或调高系统输入音量，否则分析会失真。" % peak_db)
    if stalls:
        s = stalls[0]
        out.append("第一个明显卡壳点在 %02d:%05.2f（停顿 %.2f 秒），位置在「%s」附近，回听这一段。" % (
            int(s["t"] // 60), s["t"] % 60, s["dur"], s["clause"] or "句间"))
    if not out:
        out.append("各项指标都在健康区间，继续用更长的段落和即兴表达来巩固。")
    return out


if __name__ == "__main__":
    import sys
    p = sys.argv[1]
    t = sys.argv[2] if len(sys.argv) > 2 else "大家好，我今天想和大家聊一聊，怎么样才能把话说得清楚又自然。"
    with open(p, "rb") as f:
        r = analyze(f.read(), t)
    import json
    print(json.dumps({k: v for k, v in r.items() if k not in ("syllables",)}, ensure_ascii=False, indent=2))
