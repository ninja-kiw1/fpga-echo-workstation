# -*- coding: utf-8 -*-
"""A1 部署: 上传 src/(unet_net.h 已改 256KB) -> 板上原生编译 -> 重启服务。"""
import os
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
REMOTE = "/root/tcp_image_rx"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, timeout=15,
          look_for_keys=False, allow_agent=False)
sh = lambda cmd, t=600: tuple(x.read().decode(errors="replace")
                              for x in c.exec_command(cmd, timeout=t)[1:])

c.exec_command("mkdir -p %s/src" % REMOTE)[1].read()
sftp = c.open_sftp()
for f in sorted(os.listdir(os.path.join(PROJ, "src"))):
    sftp.put(os.path.join(PROJ, "src", f), REMOTE + "/src/" + f)
    print("[up] src/%s" % f)
sftp.put(os.path.join(PROJ, "makefile"), REMOTE + "/makefile")
print("[up] makefile")
sftp.close()

print("=== board build ===")
o, e = sh("cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1; echo RC=$?" % REMOTE)
print(o)
if e.strip():
    print("[stderr]", e)

print("=== check + restart service ===")
o, e = sh("grep -n UNET_BRAM_WORDS %s/src/unet_net.h; "
          "systemctl restart tcp-image-server; sleep 2; systemctl is-active tcp-image-server" % REMOTE)
print(o)
if e.strip():
    print("[stderr]", e)
c.close()
print("DEPLOY_DONE")
