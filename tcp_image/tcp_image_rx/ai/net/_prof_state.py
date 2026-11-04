# -*- coding: utf-8 -*-
"""板子状态检查 + 收尾: 杀掉手动起的服务(注意 comm 名被截成 15 字符!),
把 tcp-image-server 拉回 active 并确认 5060 在听。"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=10,
              banner_timeout=10, auth_timeout=10,
              look_for_keys=False, allow_agent=False)
    print("ssh ok", flush=True)

    def sh(cmd, t=60):
        _i, o, e = c.exec_command(cmd, timeout=t)
        try:
            return o.read().decode(errors="replace")
        except Exception as ex:                    # noqa: BLE001
            return "[timeout: %r]" % (ex,)

    print("--- 谁占着 5060 ---", flush=True)
    print(sh("ss -ltnp 2>/dev/null | grep 5060; ps -eo pid,comm,args | grep -i tcp_image | grep -v grep"),
          flush=True)

    print("--- 杀掉手动实例 (comm = tcp_image_serve) ---", flush=True)
    print(sh("pkill -x tcp_image_serve; pkill -x tcp_image_server; sleep 2; "
             "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE"), flush=True)

    print("--- 重启 systemd 服务 ---", flush=True)
    print(sh("systemctl reset-failed tcp-image-server 2>/dev/null; "
             "systemctl restart tcp-image-server; sleep 4; "
             "systemctl is-active tcp-image-server; "
             "ss -ltnp 2>/dev/null | grep 5060; echo '(end)'"), flush=True)
    c.close()
    print("STATE_DONE", flush=True)


if __name__ == "__main__":
    main()
