# -*- coding: utf-8 -*-
"""板子诊断: 看谁在跑 / dmesg 尾部 / 是否卡住。"""
import sys

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                 # noqa: BLE001
    pass


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=8,
              banner_timeout=8, auth_timeout=8,
              look_for_keys=False, allow_agent=False)

    def sh(cmd, t=30):
        _i, o, _e = c.exec_command(cmd, timeout=t)
        try:
            return o.read().decode(errors="replace")
        except Exception as ex:                   # noqa: BLE001
            return "[timeout: %r]" % (ex,)

    print("--- ps (按 CPU 排) ---")
    print(sh("ps -eo pid,ppid,pcpu,stat,comm,args --sort=-pcpu | head -12"))
    print("--- dmesg 尾 ---")
    print(sh("dmesg | tail -8"))
    print("--- uptime/load ---")
    print(sh("uptime"))
    if len(sys.argv) > 1 and sys.argv[1] == "kill":
        print("--- 杀掉 unet_selftest ---")
        print(sh("pkill -x unet_selftest; sleep 1; ps -eo comm | grep -c unet || echo none"))
    c.close()
    print("PS_DONE")


if __name__ == "__main__":
    main()
