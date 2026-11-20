# -*- coding: utf-8 -*-
"""生成可控的“伪语音”测试信号，用来回归验证 analyzer 的语速/停顿/音节/F0 估计。

N_SYL 必须与下面 TEXT 的实际音节数一致（汉字 + 字母数字各记 1，标点不计）。
三段 8/9/9 共 26 个，和 TEXT 对得上；改了 TEXT 记得同时改 N_SYL，否则
回归报告里的“检测/期望”永远对不齐，分数也就失去意义。
"""
import math
import os
import wave

import numpy as np

SR = 16000
HERE = os.path.dirname(os.path.abspath(__file__))

TEXT = "大家好我说两句吧，今天想和大家聊一聊。其实最重要就是练习。"
N_SYL = [8, 9, 9]


def semi2hz(base, st):
    return base * (2.0 ** (st / 12.0))


def tone(dur, f0, amp=0.35, glide=1.0):
    """f0 -> f0*glide，带 1~5 次谐波的类人声音色"""
    n = max(int(dur * SR), 2)
    t = np.arange(n) / SR
    f = np.linspace(f0, f0 * glide, n)
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = np.zeros(n)
    for k, a in ((1, 1.0), (2, 0.45), (3, 0.28), (4, 0.14), (5, 0.08)):
        if f0 * k * max(1.0, glide) < SR / 2 - 400:
            sig = sig + a * np.sin(k * ph)
    sig /= 2.2
    env = np.ones(n)
    a = max(1, int(0.018 * SR))
    r = max(1, int(0.045 * SR))
    env[:a] = np.linspace(0.05, 1, a)
    env[-r:] = np.linspace(1, 0.08, r)
    return (amp * sig * env).astype(np.float32)


def silence(dur):
    return np.zeros(max(int(dur * SR), 0), dtype=np.float32)


def sentence(n_syl, f0=195.0, syl_dur=0.23, gap=0.025, rng=None, monotone=False):
    """句内音高有下降句调 + 音节间小抖动 + 句末下垂，模仿真人陈述句"""
    rng = rng or np.random.default_rng(0)
    out = []
    for i in range(n_syl):
        pos = i / max(n_syl - 1, 1)
        if monotone:
            st, glide = 0.0, 1.0
        else:
            # 全句下降 3 个半音；句末再落 2 个半音
            st = -3.0 * pos - (1.6 * pos ** 3)
            st += float(rng.normal(0, 0.9))          # 音节间抖动
            target = st - (1.0 if i == n_syl - 1 else 0.0)
            glide = 2.0 ** ((target - st) / 12.0)
        hz = semi2hz(f0, st)
        out.append(tone(syl_dur, hz, amp=0.28 + 0.10 * rng.random(), glide=glide))
        out.append(silence(gap))
    return np.concatenate(out)


def build(inter_pause, f0=195.0, syl_dur=0.23, monotone=False, drop_last=False):
    rng = np.random.default_rng(7)
    parts = []
    n_syl = N_SYL[:-1] if drop_last else N_SYL
    for i, n in enumerate(n_syl):
        if i:
            parts.append(silence(inter_pause[i - 1]))
        # 每句基频略有起伏
        parts.append(sentence(n, f0=f0 + (i - 1) * 5, syl_dur=syl_dur, rng=rng, monotone=monotone))
    return np.concatenate(parts)


def write(path, x):
    y = np.clip(x, -0.999, 0.999)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype("<i2").tobytes())


CASES = {
    # 名字: 参数
    "good":   dict(inter_pause=[0.45, 0.55], f0=195.0, syl_dur=0.23),
    "stall":  dict(inter_pause=[0.45, 1.70], f0=195.0, syl_dur=0.23),
    "slow":   dict(inter_pause=[0.45, 0.55], f0=182.0, syl_dur=0.36),
    "fast":   dict(inter_pause=[0.30, 0.30], f0=205.0, syl_dur=0.17),
    "flat":   dict(inter_pause=[0.45, 0.55], f0=195.0, syl_dur=0.23, monotone=True),
    "skip":   dict(inter_pause=[0.45, 0.55], f0=195.0, syl_dur=0.23, drop_last=True),
}

if __name__ == "__main__":
    outdir = os.path.join(HERE, "samples")
    os.makedirs(outdir, exist_ok=True)
    for name, kw in CASES.items():
        x = build(**kw)
        write(os.path.join(outdir, "synthetic_%s.wav" % name), x)
        print("wrote", name, "%.2fs" % (len(x) / SR))
