# -*- coding: utf-8 -*-
"""起停板上的 tcp-image-server 服务。用法: _board_svc.py <start|stop|status>

为什么单独做：板端自检（跑 unet_selftest 直接操作 PL）和服务（也操作 PL）**不能同时跑**，
否则会抢 conv/elt 两个核，读出假数据；重编 PL 时更是会把 PS 挂死。
"""
import sys

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"


def main():
    act = sys.argv[1] if len(sys.argv) > 1 else "status"
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=10,
              look_for_keys=False, allow_agent=False)
    if act in ("start", "stop"):
        cmd = "systemctl %s tcp-image-server; sleep 1; systemctl is-active tcp-image-server; " \
              "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE" % act
    else:
        cmd = "systemctl is-active tcp-image-server; ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE"
    _i, o, e = c.exec_command(cmd, timeout=30)
    print(o.read().decode(errors="replace").rstrip())
    err = e.read().decode(errors="replace").rstrip()
    if err:
        print("[stderr]", err)
    c.close()


if __name__ == "__main__":
    main()
