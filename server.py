# -*- coding: utf-8 -*-
"""
中文口语表达训练器 —— 本地服务（零第三方依赖）

启动：python server.py            # 默认 http://127.0.0.1:8899
"""
import json
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
HIST = os.path.join(HERE, "history")
os.makedirs(HIST, exist_ok=True)
sys.path.insert(0, HERE)

import analyzer  # noqa: E402
import passages  # noqa: E402
import plan  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("KOUYU_PORT", "8899"))


_HIST_CACHE = {"stamp": None, "rows": []}


def _hist_stamp():
    """目录指纹：文件名 + mtime。新增/删除/改写任何一条都会变。"""
    out = []
    for name in os.listdir(HIST):
        if not name.endswith(".json"):
            continue
        try:
            out.append((name, os.path.getmtime(os.path.join(HIST, name))))
        except OSError:
            continue
    out.sort()
    return hash(tuple(out))


def list_history():
    stamp = _hist_stamp()
    if _HIST_CACHE["stamp"] == stamp:
        return _HIST_CACHE["rows"]
    rows = []
    for name in sorted(os.listdir(HIST), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(HIST, name), encoding="utf-8") as f:
                d = json.load(f)
            meta = d.get("meta", {})
            res = d.get("result", {})
            # 没检测到语音的失败录音没有指标，不该出现在历史和进度里
            if not res.get("ok") or "metrics" not in res:
                continue
            rows.append({
                "id": name[:-5],
                "time": meta.get("time", ""),
                "title": meta.get("title", ""),
                "mode": meta.get("mode", ""),
                "score": res.get("score"),
                "subscores": res.get("subscores", {}),
                "rate": res.get("metrics", {}).get("整体语速_字每秒"),
                "rate_artic": res.get("metrics", {}).get("吐字速率_字每秒"),
                "speech": res.get("metrics", {}).get("说话占比"),
                "long": res.get("metrics", {}).get("长停顿次数_0.6s以上"),
                "dur": res.get("metrics", {}).get("录音总时长"),
                "dims": res.get("dims", {}),
                "reliable": bool(res.get("reliable")),
                "confidence": res.get("confidence"),
            })
        except Exception:
            continue
    rows = rows[:200]
    _HIST_CACHE["stamp"], _HIST_CACHE["rows"] = stamp, rows
    return rows


def plan_progress():
    """从历史记录里算出：最近一次的四维分、各维度最好成绩、当前建议阶段。

    不存额外状态文件——录音本身就是进度，少一个需要维护的东西。
    """
    rows = list_history()
    dims_best, dims_last = {}, {}
    # 只有够长、够多音节的录音才纳入阶段判定：
    # 0.9 秒的片段也能算出一个 flow=100，拿它当成绩会让计划永远停在第 1 周。
    reliable = [r for r in rows if r.get("reliable") and (r.get("dims") or {})]
    for r in reliable:
        for k, v in (r.get("dims") or {}).items():
            if v is None:
                continue
            dims_last.setdefault(k, v)          # rows 是倒序，第一次见到的就是最近一次
            if k not in dims_best or v > dims_best[k]:
                dims_best[k] = v
    ev = plan.evaluate(dims_last) if dims_last else {
        "stage": plan.STAGES[0]["id"], "stage_name": plan.STAGES[0]["name"],
        "ready": [], "weak": [], "all_done": False,
    }
    # 本周练习天数（按录音日期去重）
    days = sorted({(r.get("time") or "")[:10] for r in rows if r.get("time")}, reverse=True)
    return {
        "has_data": bool(dims_last),
        "last": dims_last,
        "best": dims_best,
        "stages_done": ev["all_done"],
        "current_stage": ev["stage"],
        "current_stage_name": ev["stage_name"],
        "ready": ev["ready"],
        "weak": ev["weak"],
        "sessions": len(rows),
        "reliable_sessions": len(reliable),
        "ignored_short": len(rows) - len(reliable),
        "days": days[:14],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "KouyuCoach/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------------------------------------------------------------- helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _file(self, path, ctype):
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    def _audio(self, path):
        """音频必须支持 Range 请求，否则浏览器里 <audio> 的 seekable 是 [0,0]，
        拖动播放头/跳转就会失效（点了没反应）。这是本次修的关键点。"""
        total = os.path.getsize(path)
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                start_s, end_s = m.group(1), m.group(2)
                if start_s:
                    start = int(start_s)
                    end = int(end_s) if end_s else total - 1
                else:                        # bytes=-N 表示最后 N 字节
                    n = int(end_s or 0)
                    start = max(0, total - n)
                    end = total - 1
                start = max(0, min(start, total - 1))
                end = max(start, min(end, total - 1))
                length = end - start + 1
                with open(path, "rb") as f:
                    f.seek(start)
                    chunk = f.read(length)
                self.send_response(206)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(chunk)
                return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
            if p == "/app.js":
                return self._file(os.path.join(WEB, "app.js"), "application/javascript; charset=utf-8")
            if p == "/style.css":
                return self._file(os.path.join(WEB, "style.css"), "text/css; charset=utf-8")
            if p == "/api/passages":
                return self._json(200, passages.CATALOG)
            if p == "/api/plan":
                prog = plan_progress()
                dims = []
                for d in plan.DIMENSIONS:
                    # 每个维度挂上它第一次出现的阶段门槛，页面上直接显示“目标 ≥ X”
                    tgt = None
                    for s in plan.STAGES:
                        if d["key"] in s["pass"]:
                            tgt = s["pass"][d["key"]]
                            break
                    dims.append(dict(d, target=tgt))
                return self._json(200, {
                    "dimensions": dims,
                    "stages": plan.STAGES,
                    "rhythm": plan.WEEKLY_RHYTHM,
                    "progress": prog,
                })
            if p == "/api/demo":
                # 用内置样例音频走一遍完整流程，方便先看报告长什么样
                fp = os.path.join(HERE, "samples", "demo.wav")
                if not os.path.isfile(fp):
                    return self._json(404, {"error": "缺少 samples/demo.wav"})
                return self._audio(fp)
            if p == "/api/history":
                return self._json(200, list_history())
            if p.startswith("/history/"):
                name = os.path.basename(p)
                fp = os.path.join(HIST, name)
                if not os.path.isfile(fp):
                    return self._json(404, {"error": "not found"})
                if name.endswith(".wav"):
                    return self._audio(fp)
                return self._file(fp, "application/json; charset=utf-8")
            if p == "/api/record":
                q = parse_qs(u.query)
                rid = (q.get("id") or [""])[0]
                fp = os.path.join(HIST, os.path.basename(rid) + ".wav")
                if not re.match(r"^[0-9A-Za-z_\-]+$", rid) or not os.path.isfile(fp):
                    return self._json(404, {"error": "not found"})
                return self._audio(fp)
            return self._json(404, {"error": "no route %s" % p})
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            return self._json(500, {"error": "server error"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 40 * 1024 * 1024:
            return self._json(400, {"error": "音频为空或过大（上限 40MB）"})
        body = self.rfile.read(n)
        q = parse_qs(u.query)
        if u.path != "/api/analyze":
            return self._json(404, {"error": "no route"})
        try:
            text = (q.get("text") or [""])[0]
            title = (q.get("title") or [""])[0]
            mode = (q.get("mode") or ["read"])[0]
            target = float((q.get("target") or ["4.2"])[0])
            trial = (q.get("trial") or [""])[0] == "1"   # 示例/试听：不写进历史
            result = analyzer.analyze(body, text or None, target_rate=target)
            rec_id = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % int((time.time() * 1000) % 1000)
            if result.get("ok") and not trial:
                with open(os.path.join(HIST, rec_id + ".wav"), "wb") as f:
                    f.write(body)
                payload = {
                    "meta": {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "title": title,
                             "mode": mode, "text": text, "id": rec_id},
                    "result": result,
                }
                with open(os.path.join(HIST, rec_id + ".json"), "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
            result["id"] = None if trial else rec_id
            result["trial"] = trial
            return self._json(200, result)
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("中文口语表达训练器已启动： http://%s:%d" % (HOST, PORT))
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
