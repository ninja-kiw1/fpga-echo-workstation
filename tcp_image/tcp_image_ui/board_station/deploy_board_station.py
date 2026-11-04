# -*- coding: utf-8 -*-
"""deploy_board_station.py —— 把心超工作站装到板端 DP 工作站（/data/board_station），可回滚。

板上原样保留：shim/（tkinter -> GTK3 兼容层）、app/（交付包版上位机）、board/station.py（老主界面 + TF 卡视频）。
新增：
    app_heart/   = app/ 的拷贝 + app_merge/ 里五个三方合并的文件（app / sequence / echo_ws / ctwin / progwin：
                   交付包版 + 本次心超工作站改动，基 = share/PLD_share_20261027.zip）+ heartapp / heartview / platformwin
                   + live_sources.json（现场模式信号源表，= E:/PLD/live/src/index.json）+ demo/（--demo 的两段素材）
    board/       station_heart.py（新入口）、heart_compat.py（numpy 1.13 / scipy 0.19 补丁）、verify_heart_station.py
兼容层改三个文件（shim_patch/：画布延迟合成 + 像素图 .copy() 防泄漏；原文件各备份 *.bak_lazy）。
切换只改 board/station_launcher.sh 里启动的那一行（先备份成 station_launcher.sh.bak_heart），回滚 = 还原备份 + 重启。
本地完整备份：E:/PLD/board_station_backup_20261030/（10-30 部署前从板上拉下来的整个 /data/board_station）。

    python deploy_board_station.py upload     # 传文件（不动正在跑的工作站）
    python deploy_board_station.py verify     # 板上 DP 自证（verify_heart_station.py，屏上会出窗口）
    python deploy_board_station.py switch     # 开机自启改成心超工作站（--demo），并立即重启工作站
    python deploy_board_station.py rollback   # 改回老主界面并重启
    python deploy_board_station.py status
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.dirname(HERE)
PLD = os.path.normpath(os.path.join(UI, "..", ".."))
sys.path.insert(0, os.path.join(PLD, "tcp_image", ".board_tools"))
import paramiko  # noqa: E402

ROOT = "/data/board_station"
NEW_FILES = ["heartapp.py", "heartview.py", "platformwin.py", "snapview.py", "clinical_hints.py",
             "camus_live_model.json"]     # v5.26：CAMUS 口径滚动质量灯的模型（jobs.load_roll_camus）
DEMO = [(os.path.join(PLD, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU", "A4C",
                      "ES000108 _4CH_1.avi"), "demo/ES000108 _4CH_1.avi")]
SNAPS = [os.path.join(PLD, "echo", "samples", "seq_%s_%s" % (p, v))          # 快照模式「双平面测量」的三个病例
         for p in ("patient0027", "patient0213", "patient0231") for v in ("4CH", "2CH")]
SHIM_PATCH = [("_canvas.py", "tkinter/_canvas.py"), ("_widgets.py", "tkinter/_widgets.py"), ("pil_imagetk.py", "pil_imagetk.py"),
              ("_gtkbase.py", "tkinter/_gtkbase.py")]   # 10-31 深夜：fill="both" 当成不填（多行文本框缩成一窄列）
LAUNCH_OLD = 'python3 -u "$ROOT/board/station.py" >> "$LOG" 2>&1'
LAUNCH_NEW = 'python3 -u "$ROOT/board/${STATION_ENTRY:-station_heart.py}" ${STATION_ARGS---demo} >> "$LOG" 2>&1'
ENV = "export DISPLAY=:0 XAUTHORITY=/run/lxdm/lxdm-:0.auth LANG=en_US.UTF-8 NO_AT_BRIDGE=1; "


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    return c


def sh(c, cmd, timeout=60, show=True):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    out, err = o.read().decode(errors="replace"), e.read().decode(errors="replace")
    if show:
        print("$ " + cmd[:160] + ("\n" + out if out else "") + ("\n" + err if err.strip() else ""))
    return out


def upload(c):
    sh(c, "test -d %s/app_heart || cp -a %s/app %s/app_heart; mkdir -p %s/app_heart/demo" % ((ROOT,) * 4))
    sf = c.open_sftp()
    files = [(os.path.join(HERE, "app_merge", f), "app_heart/" + f) for f in sorted(os.listdir(os.path.join(HERE, "app_merge")))
             if f.endswith(".py")]
    files += [(os.path.join(UI, f), "app_heart/" + f) for f in NEW_FILES]
    files += [(os.path.join(PLD, "live", "src", "index.json"), "app_heart/live_sources.json")]
    files += [(p, "app_heart/" + q) for p, q in DEMO]
    for snap in SNAPS:
        name = os.path.basename(snap)
        sh(c, "mkdir -p '%s/app_heart/demo/%s'" % (ROOT, name), show=False)
        files += [(os.path.join(snap, f), "app_heart/demo/%s/%s" % (name, f)) for f in sorted(os.listdir(snap))]
    files += [(os.path.join(HERE, f), "board/" + f) for f in ("station_heart.py", "heart_compat.py", "verify_heart_station.py",
                                                              "verify_heart_reg.py")]
    for src, dst in files:
        sf.put(src, ROOT + "/" + dst)
    sf.close()
    # 兼容层补丁（shim_patch/，10-31）：画布延迟合成；new_from_bytes 的像素图 .copy() 后再给 GTK（老 PyGObject 整帧泄漏，
    # 现场模式几分钟就把 2 GB 内存吃光、sshd 都 fork 不出来）。原文件各备份一次成 *.bak_lazy
    sf = c.open_sftp()
    for name, rel in SHIM_PATCH:
        sh(c, "cd %s/shim && (test -f %s.bak_lazy || cp -p %s %s.bak_lazy)" % (ROOT, rel, rel, rel), show=False)
        sf.put(os.path.join(HERE, "shim_patch", name), ROOT + "/shim/" + rel)
    sf.close()
    print("uploaded %d files" % len(files))
    sh(c, "cd %s/app_heart && python3 -m py_compile app.py sequence.py echo_ws.py ctwin.py progwin.py jobs.py heartapp.py "
          "heartview.py platformwin.py snapview.py ../board/station_heart.py ../board/heart_compat.py ../shim/tkinter/_canvas.py ../shim/tkinter/_widgets.py ../shim/tkinter/_gtkbase.py ../shim/pil_imagetk.py "
          "&& echo COMPILE_OK" % ROOT)


def verify(c):
    out = sh(c, ENV + "cd %s/board && TK_SHIM_LENIENT=1 timeout 1500 python3 -u verify_heart_station.py 2>&1 | "
                      "grep -v '^Gtk-\\|^$' | tail -60" % ROOT, timeout=1600)
    return "HEART_STATION_PASS" in out


def restart(c):
    # pkill -f 也会匹配到 ssh 命令自己的 shell：模式写成 '[s]tation…'，并且杀和起分两条命令（起的那条里有启动器路径）
    sh(c, "pkill -f '[s]tation_launcher[.]sh'; pkill -f 'board/[s]tation(_heart)?[.]py'; sleep 2; echo stopped")
    sh(c, "setsid nohup %s/board/station_launcher.sh >/dev/null 2>&1 < /dev/null & sleep 1; echo started" % ROOT)


def switch(c):
    sh(c, "cd %s/board && (test -f station_launcher.sh.bak_heart || cp -p station_launcher.sh station_launcher.sh.bak_heart)"
          % ROOT)
    sf = c.open_sftp()
    p = ROOT + "/board/station_launcher.sh"
    with sf.open(p) as f:
        txt = f.read().decode("utf-8")
    if LAUNCH_NEW not in txt:
        if LAUNCH_OLD not in txt:
            raise SystemExit("station_launcher.sh 里找不到启动行，没改")
        txt = txt.replace(LAUNCH_OLD, LAUNCH_NEW)
        with sf.open(p, "w") as f:
            f.write(txt.encode("utf-8"))
    sf.close()
    sh(c, "grep -n 'python3 -u' %s && sh -n %s && echo SYNTAX_OK" % (p, p))
    restart(c)


def rollback(c):
    sh(c, "cd %s/board && test -f station_launcher.sh.bak_heart && cp -p station_launcher.sh.bak_heart station_launcher.sh "
          "&& grep -n 'python3 -u' station_launcher.sh" % ROOT)
    restart(c)


def status(c):
    sh(c, "ps -eo pid,etime,cmd | grep -E 'station|heartapp' | grep -v grep; grep -n 'python3 -u' %s/board/station_launcher.sh; "
          "tail -5 %s/logs/station.log" % (ROOT, ROOT))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    c = connect()
    try:
        {"upload": upload, "verify": verify, "switch": switch, "rollback": rollback, "status": status}[cmd](c)
        if cmd in ("switch", "rollback"):
            time.sleep(25)
            status(c)
    finally:
        c.close()


if __name__ == "__main__":
    main()
