# -*- coding: utf-8 -*-
"""把 linux-xlnx 源码 tarball 传到板子并解包到 /root/linux-xlnx。

传 tarball(~150MB) 比传 6 万个文件快得多; 板上自带 tar/gzip。
"""
import os
import sys
import time
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
LOCAL = r"E:/fpga/my_project/_kernel_src/linux-xlnx-xlnx_rebase_v4.14_2018.3.tar.gz"
REMOTE_TGZ = "/root/linux-xlnx.tar.gz"
KDIR = "/root/linux-xlnx"
TOP = "linux-xlnx-xlnx_rebase_v4.14_2018.3"


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)

    def sh(cmd, timeout=600):
        _i, o, e = c.exec_command(cmd, timeout=timeout)
        return o.read().decode(errors="replace"), e.read().decode(errors="replace")

    if os.path.isdir(KDIR):
        out, _ = sh("ls %s | head -3; echo '---'; du -sh %s" % (KDIR, KDIR))
        print("[skip] %s already exists:\n%s" % (KDIR, out))
        c.close()
        return

    size = os.path.getsize(LOCAL)
    print("[local] tarball %d bytes (%.1f MB)" % (size, size / 1e6))

    t0 = time.time()
    last = [0]

    def cb(done, total):
        pct = done * 100 // max(total, 1)
        if pct >= last[0] + 10:
            last[0] = pct
            print("  upload %3d%%  (%.1f/%.1f MB, %.1fs)"
                  % (pct, done / 1e6, total / 1e6, time.time() - t0))

    sftp = c.open_sftp()
    sftp.put(LOCAL, REMOTE_TGZ, callback=cb, confirm=True)
    sftp.close()
    print("[up] done in %.1fs" % (time.time() - t0))

    print("[extract] ...")
    out, err = sh("cd /root && rm -rf %s && tar -xzf %s && mv %s %s && echo EXTRACT_OK"
                  % (TOP, REMOTE_TGZ, TOP, KDIR), timeout=1200)
    print(out.strip())
    if err.strip():
        print("[stderr]", err.strip()[:500])

    out, _ = sh("du -sh %s; ls %s | head -8; df -h / | tail -1" % (KDIR, KDIR))
    print(out)
    c.close()
    print("KERN_UP_DONE")


if __name__ == "__main__":
    main()
