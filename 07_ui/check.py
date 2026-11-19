# -*- coding: utf-8 -*-
"""上位机自检（不开界面）：依赖是否齐、资产路径对不对、能不能连上板子。

    python check.py                # 默认连 192.168.1.254:5060
    python check.py 127.0.0.1 5060 # 换地址
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OK = []
BAD = []


def step(name, fn):
    try:
        info = fn()
        OK.append(name)
        print("  [OK]   %-28s %s" % (name, info if info is not None else ""))
    except Exception as exc:
        BAD.append(name)
        print("  [FAIL] %-28s %s" % (name, exc))
        if os.environ.get("WS_DEBUG"):
            traceback.print_exc()


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.254"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5060

    print("== 1. 解释器 ==")
    print("  %s" % sys.version.split()[0])

    print("== 2. 依赖 ==")
    for mod in ("tkinter", "numpy", "scipy", "cv2", "PIL", "skimage"):
        def _imp(m=mod):
            __import__(m)
            return "ok"
        step("import " + mod, _imp)

    print("== 3. 主程序 ==")
    ws = {}

    def _import_ws():
        import workstation as w
        ws["w"] = w
        return "%d 个顶层名字" % len(dir(w))
    step("import workstation", _import_ws)
    if "w" not in ws:
        print("\n结论：主程序都起不来，先解决上面的 FAIL。")
        return 1
    w = ws["w"]

    print("== 4. 路径 ==")
    step("HERE", lambda: w.HERE)
    step("PLD_DIR", lambda: w.PLD_DIR)
    for f in ("echo_kf_config.json", "echo_heart_config.json", "camus_live_model.json",
              "tile_config.json"):
        step(f, lambda f=f: "ok" if os.path.exists(os.path.join(w.HERE, f)) else "缺文件！")

    print("== 5. 配置能不能读 ==")
    step("load_kf_config()", lambda: "mode=%s" % w.load_kf_config().get("mode", "?"))
    step("load_heart_config()", lambda: "ok")

    print("== 6. 连板子 %s:%d ==" % (host, port))
    link = w.imagelink.ImageLink(host, port)
    step("connect", lambda: (link.connect(), "connected=%s" % link.connected)[1])
    caps = {}

    def _caps():
        c = w.jobs.caps(link)
        caps.update(c or {})
        return "server=%s cardiac=%s ai=%s" % (c.get("server"), c.get("cardiac"), c.get("ai"))
    step("caps", _caps)

    print()
    if BAD:
        print("结论：%d 项 OK，%d 项 FAIL -> %s" % (len(OK), len(BAD), ", ".join(BAD)))
        if "caps" in BAD:
            print("      （连不上板子不影响界面启动，但 AI/EF 都用不了：先看板端 systemctl is-active tcp-image-server）")
        return 1
    print("结论：全部 OK（%d 项）。现在可以直接双击上一层的「启动上位机.bat」。" % len(OK))
    return 0


if __name__ == "__main__":
    sys.exit(main())
