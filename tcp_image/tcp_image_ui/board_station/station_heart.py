# -*- coding: utf-8 -*-
"""station_heart.py —— 板端 DP 工作站主入口（2026-10-30 起）：心超工作站 heartapp.py，与 PC 版同一份上位机代码。

    /data/board_station/app_heart/   上位机（PC 的 tcp_image_ui 与交付包版 app/ 三方合并 + heartapp / heartview / platformwin）
    /data/board_station/shim/        原来的 tkinter -> GTK3 兼容层（不改）
    /data/board_station/board/       本文件 + heart_compat.py（numpy 1.13 / scipy 0.19 补丁）+ 原 station.py / videowin.py
原来的 station.py（老主界面 app.py + TF 卡视频）原样保留；station_launcher.sh 用 STATION_ENTRY 选入口，回滚见 deploy 脚本。
TF 卡视频、板端自检两个入口挂在「平台扩展 → 更多」里（它们要单幅图的 App）。连本机 127.0.0.1:5060。

用法（station_launcher.sh 调）：
    python3 -u station_heart.py [--demo] [--mode live|keyframe|snapshot]
"""

import os
import sys

SHIM = os.environ.get("STATION_SHIM", "/data/board_station/shim")
BOARD = os.environ.get("STATION_BOARD", "/data/board_station/board")
APP = os.environ.get("STATION_APP", "/data/board_station/app_heart")
for _p in (SHIM, BOARD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bootstrap  # noqa: E402

bootstrap.install(APP)
import heart_compat  # noqa: E402

heart_compat.install()
os.chdir(APP)
import station  # noqa: E402  原工作站的 TF 卡视频 / 板端自检（它 import 时会 chdir 到老 app 目录，下面切回来）

os.chdir(APP)
import heartapp  # noqa: E402

heartapp.EXTRA_TOOLS[:] = [("TF 卡视频", station.open_video), ("板端自检", station.launch_selftest)]


def main():
    host = os.environ.get("STATION_HOST_ADDR", "127.0.0.1")
    port = os.environ.get("STATION_HOST_PORT", "5060")
    sys.argv = ["heartapp.py", host, port] + [a for a in sys.argv[1:] if a.startswith("--") or a in
                                              ("live", "keyframe", "snapshot")]
    heartapp.main()


if __name__ == "__main__":
    main()
