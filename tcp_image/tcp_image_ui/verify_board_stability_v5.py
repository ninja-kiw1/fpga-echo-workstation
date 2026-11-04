# -*- coding: utf-8 -*-
"""Long-run stability of the v5.2 board server: all five models mixed, every frame checked.

A fixed-seed random stream of N frames (default 5000) over the model library, each frame
compared with a bit-exact reference:
  fundus @512 / @128, skin @128          baseline/vectors (*.rgb + integer logits *.i8)
                                         (skin @512 cannot be requested: resolution field 512 means
                                          "the model's default", which is 128 for skin -- by design)
  echo_lv (2)     200 validation frames,  integer logits   (echo quant outputs)
  echo_inter (3)  2400 click inputs,      integer masks    (echo quant click_inputs)
  echo_seq (4)    3516 propagation inputs, integer masks   (echo quant seq_inputs)
Per frame: the board reports the requested model and resolution, every image row arrives
with good CRC / sequence, the logit plane is the 128 (or 512) result repeated, the mask
image equals logit >= thr, and the logits (models 0-2) or masks (3-4) equal the reference.
20 % of the frames also ask for the PL preprocessing image with a random mode: mode 5
(pass-through) must return the input byte for byte, the others the same bytes every time
for the same input and mode. AI input is always the raw RGB, so the mode never changes AI.
Board health over SSH after a 20-frame warm-up (buffers allocated) and at the end, plus the
server RSS every 500 frames: service restarts, RSS (leak check), memory, temperatures (ZynqMP
AMS), error lines of the CURRENT boot in the service journal and dmesg (the board has no RTC,
its clock restarts in 2021, so --since would also count older boots).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_stability_v5.py [--frames 5000]
Writes board_check_v5/stability_v5.json.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / "tcp_image" / ".board_tools"))
import confidence  # noqa: E402
import imagelink   # noqa: E402
import paramiko    # noqa: E402
import proto       # noqa: E402

VEC = PLD / "tcp_image" / "baseline" / "vectors"
ECHO = PLD / "echo" / "results" / "quant"
OUT = HERE / "board_check_v5"

HEALTH = r"""
pid=$(systemctl show -p MainPID --value tcp-image-server)
echo "restarts=$(systemctl show -p NRestarts --value tcp-image-server)"
echo "pid=$pid"
echo "active_since=$(systemctl show -p ActiveEnterTimestamp --value tcp-image-server)"
grep -E '^(VmRSS|VmHWM|Threads):' /proc/$pid/status | tr -s ' \t' ' ' | sed 's/: /=/'
free -k | awk '/^Mem:/ {print "mem_used_kb=" $3; print "mem_avail_kb=" $7}'
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
for d in /sys/bus/iio/devices/iio:device*; do
  [ "$(cat $d/name 2>/dev/null)" = "ams" ] || continue
  for r in $d/in_temp*_raw; do
    b=${r%_raw}; n=$(basename $b)
    echo "temp_$n=$(cat $r),$(cat ${b}_offset 2>/dev/null || cat $d/in_temp_offset 2>/dev/null || echo 0),$(cat ${b}_scale 2>/dev/null || cat $d/in_temp_scale 2>/dev/null || echo 1)"
  done
done
echo "dmesg_err=$(dmesg | grep -ciE 'error|fault|oops|timeout')"
"""


def ssh():
    c = paramiko.SSHClient()
    known = HERE / "board_check_v4" / "known_hosts"
    if known.exists():
        c.load_host_keys(str(known))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    return c


def run(c, cmd):
    _, out, _ = c.exec_command(cmd + " 2>&1", timeout=60)
    return out.read().decode("utf-8", "replace")


def health(c):
    h = {}
    for line in run(c, HEALTH).splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.startswith("temp_"):
            raw, off, scale = (float(x) for x in v.split(","))
            h[k] = round((raw + off) * scale / 1000.0, 2)          # millidegree -> degC
        else:
            h[k] = v.strip()
    return h


def rss_only(c):
    """Mid-run sample: server RSS only. (A full journal scan takes >10 s once the journal is large;
    the image link then idles past the server's 10 s receive timeout and gets closed -- seen on v5.4.)"""
    return run(c, "grep VmRSS /proc/$(systemctl show -p MainPID --value tcp-image-server)/status").split(":")[-1].strip()


def journal_cursor(c):
    out = run(c, "journalctl -u tcp-image-server -b -n 0 --show-cursor --no-pager")
    return out.split("cursor:", 1)[-1].strip() if "cursor:" in out else ""


def journal_errors_after(c, cursor):
    # -b as well: without a real-time clock older boots carry LATER timestamps and would sort after the cursor
    cmd = "journalctl -u tcp-image-server -b --no-pager %s | grep -ciE 'error|fail|timeout|abort'" % (
        "--after-cursor='%s'" % cursor if cursor else "")
    return int((run(c, cmd).strip() or "0").splitlines()[-1])


def load_cases():
    cases = []
    for name, model, res, thr in (("fundus", 0, 512, -3), ("fundus", 0, 128, -3), ("skin", 1, 128, 1)):
        rgb = np.fromfile(VEC / ("%s_512.rgb" % name), np.uint8).reshape(512, 512, 3)
        ref = np.fromfile(VEC / ("%s_%d.i8" % (name, res)), np.int8).astype(np.int64).reshape(res, res)
        cases.append(dict(group="%s@%d" % (name, res), model=model, res=res, thr=thr, items=[(rgb, ref)], logit=True))
    x = np.load(PLD / "echo" / "data" / "cache" / "val_img512.npy", mmap_mode="r")
    echo_items = [(None, i) for i in range(200)]                    # loaded lazily
    cases.append(dict(group="echo_lv@128", model=2, res=128, thr=1, items=echo_items, logit=True, lazy="echo"))
    for group, model, sub, name in (("echo_inter@128", 3, "inter2_r128/click_inputs", "from_f1"),
                                    ("echo_seq@128", 4, "seq_none_ex/seq_inputs", "fwd")):
        files = [(ECHO / sub / ("%s_rgb.npy" % n), ECHO / sub / ("%s_mask.npy" % n))
                 for n in ((name, "from_empty") if model == 3 else (name, "bwd"))]
        rgbs = [np.load(f[0], mmap_mode="r") for f in files]
        masks = [np.load(f[1], mmap_mode="r") for f in files]
        items = []
        for r, m in zip(rgbs, masks):
            r2 = r.reshape(-1, 128, 128, 3)
            m2 = m.reshape(-1, 128, 128)
            items += [(r2, m2, j) for j in range(len(r2))]
        cases.append(dict(group=group, model=model, res=128, thr=1, items=items, logit=False))
    return cases, x


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frames", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--out", default="stability_v5.json", help="file name in board_check_v5/")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    cases, xval = load_cases()
    weights = np.array([0.08, 0.04, 0.08, 0.25, 0.25, 0.30])     # fundus512, fundus128, skin128, models 2, 3, 4
    warmup = 20
    rng = np.random.default_rng(args.seed)
    plan = rng.choice(len(cases), size=args.frames, p=weights / weights.sum())
    c = ssh()
    t_start = datetime.now(timezone.utc)
    boot = health(c)
    before, rss = None, []

    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
    stats = {cs["group"]: dict(frames=0, mismatches=[], ai_ms=[], rt_ms=[]) for cs in cases}
    proc_digest, proc_checks, failures, reconnects = {}, dict(passthrough=0, deterministic=0, bad=[]), [], 0
    t0 = time.time()
    for n, ci in enumerate(plan):
        if n == warmup:
            before = health(c)
            cursor = journal_cursor(c)
            print("health after warm-up", json.dumps(before), flush=True)
        if n > warmup and n % 500 == 0:
            rss.append((n, rss_only(c)))
        cs = cases[ci]
        k = int(rng.integers(len(cs["items"])))
        item = cs["items"][k]
        if cs.get("lazy") == "echo":
            i = item[1]
            rgb = np.repeat(np.asarray(xval[i])[:, :, None], 3, axis=2)
            ref = np.load(ECHO / "lv_r128" / "outputs" / ("val_%03d.npy" % i)).astype(np.int64)
        elif cs["logit"]:
            rgb, ref = item
        else:
            arr, masks, j = item
            rgb = np.repeat(np.repeat(np.asarray(arr[j]), 4, 0), 4, 1)
            ref = np.asarray(masks[j])
        want_proc = rng.random() < 0.2
        mode = int(rng.integers(0, 6)) if want_proc else 5
        flags = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK | (proto.FLAG_ECHO_PROC if want_proc else 0)
        payload = np.ascontiguousarray(rgb, np.uint8).tobytes()
        try:
            imgs, tim, rows, st = link.round_trip(payload, mode=mode, flags=flags,
                                                  aiparam=proto.make_aiparam(0, cs["thr"], cs["res"], cs["model"]))
        except Exception as e:                       # count, reconnect, keep going
            failures.append(dict(frame=n, group=cs["group"], error=repr(e)))
            reconnects += 1
            link.close()
            continue
        g = stats[cs["group"]]
        g["frames"] += 1
        ok = (st is not None and st["model"] == cs["model"] and st["ai_res"] == cs["res"] and st["ai_us"] > 0
              and all(r["crc_errors"] == 0 and r["seq_errors"] == 0 and r["rows_ok"] == 512 for r in rows.values()))
        kk = 512 // cs["res"]
        plane = np.frombuffer(confidence.logit_plane(imgs["logit"]), np.uint8).reshape(512, 512).astype(np.int64) - proto.LOGIT_IMG_OFFSET
        lg = plane[::kk, ::kk]
        ok = ok and np.array_equal(plane, np.repeat(np.repeat(lg, kk, 0), kk, 1))
        mask_img = np.frombuffer(imgs["mask"], np.uint8).reshape(512, 512, 3)[:, :, 0] > 0
        ok = ok and np.array_equal(mask_img, np.repeat(np.repeat(lg >= cs["thr"], kk, 0), kk, 1))
        ok = ok and (np.array_equal(lg, ref) if cs["logit"] else np.array_equal(lg >= cs["thr"], ref))
        if want_proc:
            if mode == 5:
                good = imgs.get("proc") == payload
                proc_checks["passthrough"] += 1
            else:
                key = (hashlib.sha1(payload).hexdigest(), mode)
                dig = hashlib.sha1(imgs.get("proc", b"")).hexdigest()
                good = proc_digest.setdefault(key, dig) == dig
                proc_checks["deterministic"] += 1
            if not good:
                proc_checks["bad"].append(dict(frame=n, mode=mode))
            ok = ok and good
        if not ok:
            g["mismatches"].append(n)
        g["ai_ms"].append(st["ai_us"] / 1000 if st else 0)
        g["rt_ms"].append(tim["total_ms"])
        if (n + 1) % 250 == 0:
            bad = sum(len(v["mismatches"]) for v in stats.values())
            print("%d/%d frames, mismatches %d, failures %d (%.0fs)" % (n + 1, args.frames, bad, len(failures), time.time() - t0),
                  flush=True)
    link.close()
    seconds = time.time() - t0
    after = health(c)
    journal_new_errors = journal_errors_after(c, cursor)
    c.close()
    pct = lambda v, q: float(np.percentile(v, q)) if v else None
    groups = {}
    for k, v in stats.items():
        groups[k] = dict(frames=v["frames"], mismatches=len(v["mismatches"]), first_mismatches=v["mismatches"][:10],
                         ai_ms=dict(mean=float(np.mean(v["ai_ms"])) if v["ai_ms"] else None, p99=pct(v["ai_ms"], 99),
                                    max=max(v["ai_ms"]) if v["ai_ms"] else None),
                         rt_ms=dict(mean=float(np.mean(v["rt_ms"])) if v["rt_ms"] else None, p50=pct(v["rt_ms"], 50),
                                    p95=pct(v["rt_ms"], 95), p99=pct(v["rt_ms"], 99), max=max(v["rt_ms"]) if v["rt_ms"] else None))
    total_bad = sum(g["mismatches"] for g in groups.values())
    done = sum(g["frames"] for g in groups.values())
    report = dict(utc_start=t_start.isoformat(), frames_planned=args.frames, frames_done=done, seed=args.seed,
                  seconds=seconds, mismatching_frames=total_bad, failures=failures[:20], failure_count=len(failures),
                  reconnects=reconnects, proc_checks=dict(passthrough=proc_checks["passthrough"],
                                                          deterministic=proc_checks["deterministic"],
                                                          bad=proc_checks["bad"][:10]),
                  groups=groups, health_at_start=boot, health_after_warmup=before, health_after=after, rss_samples=rss,
                  journal_error_lines_delta=journal_new_errors,
                  dmesg_error_lines_delta=int(after["dmesg_err"]) - int(before["dmesg_err"]),
                  same_server_process=before.get("pid") == after.get("pid"),
                  restarts_delta=int(after.get("restarts", 0)) - int(before.get("restarts", 0)))
    report["pass"] = bool(total_bad == 0 and not failures and report["same_server_process"]
                          and report["restarts_delta"] == 0 and done == args.frames
                          and report["journal_error_lines_delta"] == 0 and report["dmesg_error_lines_delta"] == 0)
    (OUT / args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, g in groups.items():
        print("%-15s frames %5d mismatches %d  AI %.1f ms (max %.1f)  round trip mean %.0f p99 %.0f max %.0f ms" % (
            k, g["frames"], g["mismatches"], g["ai_ms"]["mean"] or 0, g["ai_ms"]["max"] or 0,
            g["rt_ms"]["mean"] or 0, g["rt_ms"]["p99"] or 0, g["rt_ms"]["max"] or 0))
    print("health after", json.dumps(after), "RSS samples", rss)
    print("STABILITY_V5_PASS" if report["pass"] else "STABILITY_V5_FAIL", "%d frames in %.0f s" % (done, seconds), flush=True)
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
