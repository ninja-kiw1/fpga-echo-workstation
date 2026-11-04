# -*- coding: utf-8 -*-
"""SSH 连通性诊断: 每一步都打印 + 显式超时。"""
import time
import paramiko

print("step0: python started", flush=True)
t = time.time()
try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root",
              timeout=10, banner_timeout=10, auth_timeout=10,
              look_for_keys=False, allow_agent=False)
    print("step1: connected in %.2fs" % (time.time() - t), flush=True)
    _i, o, e = c.exec_command("uptime; echo OK", timeout=20)
    print("step2: %s" % o.read().decode(errors="replace").strip(), flush=True)
    c.close()
    print("step3: closed", flush=True)
except Exception as ex:
    print("FAIL after %.2fs: %r" % (time.time() - t, ex), flush=True)
print("SSHPING_DONE", flush=True)
