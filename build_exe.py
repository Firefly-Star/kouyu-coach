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

# PyInstaller 会自动分析大部分依赖，但有两类东西经常漏，必须显式补：
#
# 1. Python 自带的 pyd 扩展（DLLs/ 下的）**以及它们的伴随 dll**。
#    拉了 onnxruntime / ctranslate2 之后，pkg_resources 启动钩子会导入
#    plistlib → xml.parsers.expat → pyexpat。pyexpat.pyd 依赖
#    libexpat.dll，只加 pyd 不加 dll 照样报
#    "ImportError: DLL load failed while importing pyexpat"，程序起不来。
#    —— 这个坑踩了两次：第一次漏 pyd，第二次漏它依赖的 libexpat.dll。
# 2. onnxruntime / ctranslate2 自带的 dll（用 --collect-binaries 收）。
#
# 路径都相对于当前 Python 解释器，写死会绑到某台机器，所以用 sysconfig 反查。
EXTRA_BINARIES = [
    "pyexpat.pyd", "libexpat.dll",
    "unicodedata.pyd", "_ctypes.pyd",
    "libcrypto-3-x64.dll", "libssl-3-x64.dll",
]


def _extra_binary_args():
    import sysconfig
    dlls = sysconfig.get_path("platstdlib") or os.path.join(sys.prefix, "DLLs")
    search = [
        os.path.join(sys.prefix, "DLLs"),
        os.path.join(sys.prefix, "Library", "bin"),
        os.path.join(sys.prefix, "Library", "usr", "bin"),
        dlls,
    ]
    out = []
    for name in EXTRA_BINARIES:
        for d in search:
            p = os.path.join(d, name)
            if os.path.exists(p):
                out += ["--add-binary", p + SEP + "."]
                break
        else:
            print("  [warn] 没找到 %s，打包后可能起不来" % name)
    return out


def main():
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--name", NAME,
        # 静态资源：web 前端 + samples 示例音频
        "--add-data", os.path.join(HERE, "web") + SEP + "web",
        "--add-data", os.path.join(HERE, "samples") + SEP + "samples",
        # onnxruntime 靠动态加载，PyInstaller 的静态分析抓不到它的 dll
        "--collect-binaries", "onnxruntime",
        "--collect-binaries", "ctranslate2",
        # asr.py 里 faster_whisper 是**函数内延迟导入**（避免启动时加载模型），
        # PyInstaller 的静态分析看不见函数体内的 import，整个包被漏掉，
        # 于是 exe 里 asr.available() 恒为 False、内容分析永远不可用。
        # 这类"动态导入"必须显式 --hidden-import，光靠自动分析没用。
        "--hidden-import", "faster_whisper",
        "--collect-all", "faster_whisper",
        # numpy 会被自动分析到；这里显式排除几个用不上的大包，减小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "PIL",
    ] + _extra_binary_args() + [
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
