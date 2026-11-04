"""v5.17: loaded latency of the DEPLOYED server (systemd unit, Nice=-20 drop-in, J1 on), measured from the PC.
hog (cpu4 / mem4, /root/perf_j/hog) runs on the board while the PC sends frames; latency = the server's own
stats-block ai_us (U-Net on the board), plus PC round trip. BoardCheck GUI paused during the run (as run_board.py).
    E:/PLD/skin/.venv/Scripts/python.exe j3s_server.py [OUT.json]  -> results/j3s_server.json (or results/OUT.json)
"""
import json, statistics as st, sys, time
from pathlib import Path
from bt import HERE, connect, run

sys.path.insert(0, str(HERE.parent / "tcp_image/tcp_image_ui"))
import imagelink, proto  # noqa: E402

GUI = ('pids=$(pgrep -f "^python3 /root/Fz3b_BoardCheck/gui/board_check_gui.py$" || true); '
       'if [ -n "$pids" ]; then kill -{sig} $pids; fi')
CASES = (("512", 0, 512, 30), ("128", 1, 128, 150))     # tag, model, res, frames (first dropped)


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def main():
    c = connect()
    rgb_path = HERE / "results/fundus512.rgb"
    if not rgb_path.exists():
        c.open_sftp().get("/root/perf_j/fundus512.rgb", str(rgb_path))
    rgb = rgb_path.read_bytes()
    t, _ = run(c, "sha256sum /root/tcp_image_rx/tcp_image_server; systemctl show -p Nice tcp-image-server; "
                  "ps -L -o lwp,ni,comm -C tcp_image_serve; " + GUI.format(sig="STOP"))
    res = {"server": t, "conds": {}}
    flags = proto.FLAG_STATS | proto.FLAG_NO_IMAGE
    try:
        for cond in ("idle", "cpu4", "mem4"):
            if cond != "idle":
                run(c, "cd /root/perf_j && (nohup ./hog %s 4 3000 > /dev/null 2>&1 &) ; sleep 3" % cond[:3])
            link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
            try:
                for tag, model, r, n in CASES:
                    ai, tot = [], []
                    for i in range(n):
                        _, times, _, s = link.round_trip(rgb, mode=0, flags=flags,
                                                        aiparam=proto.make_aiparam(0, -3, r, model=model))
                        if i:
                            ai.append(s["ai_us"] / 1000.0); tot.append(times["total_ms"])
                    row = {"n": len(ai), "ai_p50": st.median(ai), "ai_p99": pct(ai, 0.99), "ai_max": max(ai),
                           "rt_p50": st.median(tot)}
                    res["conds"]["%s/%s" % (cond, tag)] = row
                    print(cond, tag, {k: round(v, 2) for k, v in row.items()}, flush=True)
            finally:
                link.close()
                run(c, "pkill -x hog; sleep 2; true")
    finally:
        run(c, "pkill -x hog; " + GUI.format(sig="CONT") + "; systemctl is-active tcp-image-server")
    (HERE / "results" / (sys.argv[1] if len(sys.argv) > 1 else "j3s_server.json")).write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
