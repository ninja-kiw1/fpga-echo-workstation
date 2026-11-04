"""跑完三种图样, 自动比对板上 frame CRC16 与 PC 端算出的期望值。
用法: python run_multi.py
"""
import os
import re
import subprocess
import sys
import time

import paramiko

HOST = "192.168.1.254"
REMOTE = "/root/tcp_image_rx"
REPO = r"E:/fpga/my_project/tcp_image_rx"

# pattern -> 期望的整幅 CRC16 (PC 端独立算出)
CASES = [("bars", "0xF9F9"), ("checker", "0x1529"), ("gradient", "0x7BB7")]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="root", password="root", timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=15):
    _in, out, _err = cli.exec_command(cmd, timeout=timeout)
    try:
        return out.read().decode(errors="replace").rstrip()
    except Exception:
        return "(read timeout)"


def fire(cmd):
    cli.exec_command(cmd)
    time.sleep(2)


results = []
port = 5100
for pattern, expect in CASES:
    port += 1
    log = "/tmp/rx_multi_%d.log" % port
    run("pkill -9 -f tcp_image_serve 2>/dev/null; sleep 1; echo ok")
    fire("cd %s && setsid nohup ./tcp_image_server -p %d --anon-buf --no-dma -n 1 "
         "> %s 2>&1 < /dev/null &" % (REMOTE, port, log))

    cmd = [sys.executable, os.path.join(REPO, "script", "send_image.py"),
           "-H", HOST, "-P", str(port), "--pattern", pattern]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    time.sleep(3)

    logtext = run("cat %s" % log, 20)
    m = re.search(r"frame CRC16 = (0x[0-9A-Fa-f]{4})", logtext)
    m2 = re.search(r"rows_ok=(\d+)/512, crc_err=(\d+), seq_err=(\d+)", logtext)
    got = m.group(1) if m else None
    rows = m2.group(1) if m2 else "?"
    cerr = m2.group(2) if m2 else "?"
    serr = m2.group(3) if m2 else "?"
    ok = (got is not None and got.upper() == expect.upper()
          and rows == "512" and cerr == "0" and serr == "0")
    results.append((pattern, expect, got, rows, cerr, serr, ok))

print("\n================ SUMMARY ================")
print("%-9s %-8s %-8s %-6s %-5s %-5s %s" % ("pattern", "expect", "got", "rows", "cerr", "serr", "result"))
for pat, exp, got, rows, cerr, serr, ok in results:
    print("%-9s %-8s %-8s %-6s %-5s %-5s %s"
          % (pat, exp, got, rows, cerr, serr, "PASS" if ok else "FAIL"))
nfail = sum(1 for r in results if not r[6])
print("failures: %d / %d" % (nfail, len(results)))
print("MULTI_DONE")

cli.close()
