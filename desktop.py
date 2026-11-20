# -*- coding: utf-8 -*-
"""口语训练台 —— 桌面版入口（供 PyInstaller 打包）

打包成 exe 后有两个和「直接跑 server.py」不同的地方，这个文件专门处理：

1. **资源路径**：PyInstaller 会把 web/ 和 samples/ 解到 sys._MEIPASS 临时目录，
   程序退出就没了。所以静态资源从这里读。

2. **数据目录**：录音和进度绝对不能写进 _MEIPASS，否则一关程序全丢。
   exe 版本把 history/ 放在 exe 同级目录下的 data/history/，
   这样双击 exe、拷走整个文件夹，进度都跟着走。

另外它还负责：
- 单实例：已经在跑就直接打开浏览器，不会起第二个服务
- 端口：默认 8899，被占用就往后找
- 自动开浏览器；关不掉浏览器时也打印地址供手动打开
"""
import os
import socket
import sys
import threading
import time
import webbrowser

FROZEN = getattr(sys, "frozen", False)


def resource_dir():
    """只读资源（web、samples）所在目录。"""
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """可写数据（history）所在目录：exe 同级，便携。"""
    if FROZEN:
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
    return os.path.dirname(os.path.abspath(__file__))


def pick_port(host, start=8899, tries=20):
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
            except OSError:
                continue
            return p
    raise SystemExit("找不到可用端口（试过 %d-%d）" % (start, start + tries - 1))


def already_running(port):
    """端口上跑的是不是**本程序**？

    不能只看“端口能连上”——用户可能刚好有别的程序占着 8899，
    那样会误判成“已在运行”，然后打开浏览器指向一个不相干的页面就退出了。
    所以这里要真的请求一下 /api/plan，确认返回的是本程序的接口。
    """
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/plan" % port, timeout=1.2) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return False
    # 本程序的 /api/plan 一定同时带这三样
    return bool(data.get("dimensions") and data.get("stages") and data.get("rhythm"))


def find_existing(start=8899, tries=20):
    """在端口段里找**已经在运行的本程序**，找到就返回它的端口。"""
    for p in range(start, start + tries):
        if already_running(p):
            return p
    return None


def main():
    res = resource_dir()
    dat = data_dir()
    os.makedirs(os.path.join(dat, "history"), exist_ok=True)

    # server.py 用模块级常量定位资源，这里在 import 前把两个目录喂给它
    os.environ["KOUYU_WEB"] = os.path.join(res, "web")
    os.environ["KOUYU_SAMPLES"] = os.path.join(res, "samples")
    os.environ["KOUYU_HISTORY"] = os.path.join(dat, "history")

    # 单实例：先看是不是已经有一个自己在跑（是就直接开浏览器，不起第二个）
    existing = find_existing()
    if existing:
        url = "http://127.0.0.1:%d" % existing
        print("口语训练台已经在运行：%s" % url)
        webbrowser.open(url)
        return

    # 没在跑：用指定端口，或挑一个空闲的
    port = int(os.environ.get("KOUYU_PORT") or pick_port("127.0.0.1"))
    os.environ["KOUYU_PORT"] = str(port)
    url = "http://127.0.0.1:%d" % port

    import server  # noqa: E402  （必须放在环境变量设置之后）

    srv = server.make_server(port)
    print("=" * 56)
    print("  口语训练台已启动")
    print("  %s" % url)
    print("  录音保存在：%s" % os.path.join(dat, "history"))
    print("  关掉这个窗口即停止服务")
    print("=" * 56)

    threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
