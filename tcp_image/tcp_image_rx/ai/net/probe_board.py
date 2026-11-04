# -*- coding: utf-8 -*-
"""P4-0: 只读探测开发板现状(不写任何 PL 无从机地址)。

用法: python probe_board.py
"""
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"

CMDS = [
    ("uptime",       "uptime"),
    ("kernel",       "uname -r"),
    ("pl_state",     "cat /sys/class/fpga_manager/fpga0/state"),
    ("fpgadrv",      "lsmod | grep -i fpga || echo '(no fpga module)'"),
    ("server_units", "systemctl is-active tcp-image-server; systemctl is-enabled tcp-image-server"),
    ("server_args",  "grep -h ExecStart /etc/systemd/system/tcp-image-server.service"),
    ("root_files",   "ls -la /root/tcp_image_rx/ | head -40"),
    ("bitfiles",     "ls -la /lib/firmware/ 2>/dev/null | head -20"),
    ("ai_bins",      "ls -la /root/*.bin /root/tcp_image_rx/*.bin 2>/dev/null || echo none"),
    ("pl_clk",       "cat /sys/kernel/debug/clk/pl0_ref/clk_rate 2>/dev/null || echo n/a"),
    ("mem",          "free -m | head -3"),
]


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    for label, cmd in CMDS:
        _, o, e = c.exec_command(cmd, timeout=20)
        out = o.read().decode(errors="replace").rstrip()
        err = e.read().decode(errors="replace").rstrip()
        print("=== %s ===" % label)
        if out:
            print(out)
        if err:
            print("[stderr]", err)
    c.close()


if __name__ == "__main__":
    main()
