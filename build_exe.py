# -*- coding: utf-8 -*-
"""打包成 exe：python build_exe.py

产物：dist/KouyuCoach/KouyuCoach.exe（整个文件夹即绿色版，可打包成 zip 分发）

为什么用 --onedir 而不是 --onefile
--------------------------------
--onefile 每次启动都要把 numpy 等依赖解包到 %TEMP%，慢（2~3 秒），
而且在权限收紧的机器上会因为解包目录写不进去直接崩
（`Failed to extract VCRUNTIME140.dll: Permission denied`）。
--onedir 启动即用、不解包、更稳，是 PyInstaller 官方推荐的发布方式。

注意事项
--------
* web/ 和 samples/ 必须一起打进去（--add-data），否则 exe 起来是白屏。
* 不打 --windowed：保留控制台窗口，用户能看到地址和「关掉窗口即停止服务」，
  出问题也能看到报错。想做成纯 GUI 再去掉这个选项。
* 录音不在这里——运行时落在 exe 同级的 data/ 下，拷走文件夹进度就跟着走。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = ";" if os.name == "nt" else ":"      # PyInstaller 的 --add-data 用平台分隔符

NAME = "KouyuCoach"


def main():
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--name", NAME,
        # 静态资源：web 前端 + samples 示例音频
        "--add-data", os.path.join(HERE, "web") + SEP + "web",
        "--add-data", os.path.join(HERE, "samples") + SEP + "samples",
        # numpy 会被自动分析到；这里显式排除几个用不上的大包，减小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "PIL",
        os.path.join(HERE, "desktop.py"),
    ]
    print(" ".join(args))
    rc = subprocess.call(args, cwd=HERE)
    if rc != 0:
        raise SystemExit("打包失败，退出码 %d" % rc)

    exe = os.path.join(HERE, "dist", NAME, NAME + ".exe")
    if not os.path.exists(exe):
        raise SystemExit("没找到产物：%s" % exe)
    total = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(os.path.join(HERE, "dist", NAME))
        for f in fs
    )
    print("\n打包完成：%s" % exe)
    print("整个 dist/%s/ 文件夹即绿色版（%.1f MB）" % (NAME, total / 1024 / 1024))

    # 顺手清掉 PyInstaller 的中间目录，保持仓库干净
    for junk in ("build", NAME + ".spec"):
        p = os.path.join(HERE, junk)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    print("已清理 build/ 与 spec 中间产物")


if __name__ == "__main__":
    main()
