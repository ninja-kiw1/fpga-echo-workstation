# -*- coding: utf-8 -*-
"""把"开机自动加载 PL 比特流"这一环装到板子上。

现状(2026-10-02 查过): 业务程序 / plbram.ko / /lib/firmware/ai_sys.bin 都已经在
磁盘上并设为开机自启; **唯一缺的是"开机把我们的比特流烧进 PL"** —— 出厂 boot.bin
带的是厂商比特流, 上电后 PL 是厂商设计。本脚本补上这一环。

用法:
  python _boot_install.py check     # 只看现状, 不动东西
  python _boot_install.py install   # 装脚本 + unit, enable (不重启)
  python _boot_install.py now       # 立刻跑一次加载脚本, 看输出
  python _boot_install.py reboot    # 重启板子 (固化验收) —— 会断网几十秒
"""
import os
import sys
import time
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
LOCAL_BOOT = os.path.join(PROJ, "boot")

KSH = "/usr/local/sbin/load_ai_sys.sh"
UNIT = "/etc/systemd/system/ai-sys-bitstream.service"


def sh(c, cmd, timeout=120):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    return c


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    c = connect()

    if mode in ("check", "install"):
        out, _ = sh(c, "echo '--- 已 enable 的服务 ---'; "
                       "systemctl list-unit-files | grep -E 'tcp-image|plbram|ai-sys'; "
                       "echo '--- 比特流文件 ---'; ls -l /lib/firmware/ai_sys.bin; "
                       "echo '--- 现在 PL 里是不是我们的 ---'; "
                       "python3 -c \"import mmap,os,struct;fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC);"
                       "m=mmap.mmap(fd,0x1000,mmap.MAP_SHARED,mmap.PROT_READ|mmap.PROT_WRITE,offset=0xA0000000);"
                       "print('VERSION @0xA0000010 = 0x%08X' % struct.unpack_from('<I',m,0x10)[0])\"")
        print(out)

        if mode == "install":
            sftp = c.open_sftp()
            sftp.put(os.path.join(LOCAL_BOOT, "load_ai_sys.sh"), KSH)
            sftp.put(os.path.join(LOCAL_BOOT, "ai-sys-bitstream.service"), UNIT)
            sftp.close()
            out, err = sh(c, "chmod 0755 %s; systemctl daemon-reload; "
                             "systemctl enable ai-sys-bitstream.service; "
                             "systemctl is-enabled ai-sys-bitstream.service; "
                             "systemctl list-unit-files | grep -E 'tcp-image|plbram|ai-sys'"
                          % KSH)
            print(out)
            if err.strip():
                print("[stderr]", err.strip())

    elif mode == "now":
        out, err = sh(c, "sh %s 2>&1; echo RC=$?" % KSH)
        print(out)
        if err.strip():
            print("[stderr]", err.strip())

    elif mode == "reboot":
        print("[reboot] 现在重启; 等 60-90 s 再连")
        try:
            sh(c, "sync; (sleep 1; reboot) >/dev/null 2>&1 &", timeout=10)
        except Exception as e:
            print("(连接被重启切断, 正常: %s)" % e)
        c.close()
        for i in range(30):
            time.sleep(5)
            try:
                c2 = connect()
                out, _ = sh(c2, "uptime; echo '---'; "
                                "systemctl is-active ai-sys-bitstream plbram tcp-image-server; "
                                "echo '---'; cat /sys/class/fpga_manager/fpga0/state; "
                                "echo '---'; lsmod | head -6")
                print("[%ds] 板子回来了:\n%s" % ((i + 1) * 5, out))
                c2.close()
                break
            except Exception:
                print("  [%ds] 还没起来..." % ((i + 1) * 5))
        print("REBOOT_TEST_DONE")
        return

    else:
        print("unknown mode: %s" % mode)

    c.close()
    print("BOOT_INSTALL_DONE")


if __name__ == "__main__":
    main()
