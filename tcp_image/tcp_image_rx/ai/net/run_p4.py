# -*- coding: utf-8 -*-
"""P4-4: 上板跑 32x32 整网, 逐节点与 HLS C 仿真的 golden 比对。

流程: sftp 上传 vectors dir + unet_board.py -> 板上跑 -> 拉回 -> 逐节点比对。
用法:  python run_p4.py [vectors目录] [节点数上限]
       (默认 vectors_small / 30; 节点数上限用于调试时只跑到前 N 个)
"""
import os
import struct
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
HERE = r"E:/fpga/my_project/tcp_image_rx/ai/net"
REMOTE = "/root/tcp_image_rx"


def main():
    vec = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "vectors_small")
    max_node = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    VEC = vec
    LOCAL_OUT = os.path.join(HERE, "net_out", os.path.basename(vec.rstrip("/\\")))
    os.makedirs(LOCAL_OUT, exist_ok=True)

    node_names = [l.split()[0] for l in open(os.path.join(VEC, "network.txt")) if l.strip()]

    # 先做一次主机端自洽检查: INPUT 节点的 golden 应该就等于 input.bin
    _in = open(os.path.join(VEC, "input.bin"), "rb").read()
    _gx = open(os.path.join(VEC, "golden", "x.bin"), "rb").read()
    assert _in == _gx, "golden/x.bin != input.bin -- vector generation is inconsistent"
    print("[chk] golden/x.bin == input.bin (%d bytes) OK" % len(_in))

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    _i, o, e = c.exec_command("mkdir -p %s/vectors_small/params %s/net_out" % (REMOTE, REMOTE))
    o.read()
    sftp = c.open_sftp()

    print("[up] unet_board.py + vectors_small/ ...")
    sftp.put(os.path.join(HERE, "unet_board.py"), REMOTE + "/unet_board.py")
    for sub in ("network.txt", "input.bin", "plan.json"):
        sftp.put(os.path.join(VEC, sub), REMOTE + "/vectors_small/" + sub)
    for name in os.listdir(os.path.join(VEC, "params")):
        sftp.put(os.path.join(VEC, "params", name), REMOTE + "/vectors_small/params/" + name)
    sftp.close()

    print("[run] on board ...")
    cmd = ("cd %s && rm -rf net_out && timeout 900 python3 unet_board.py vectors_small net_out 2>&1; "
           "echo RC=$?") % REMOTE
    _i, o, e = c.exec_command(cmd, timeout=960)
    log = o.read().decode(errors="replace")
    print(log.rstrip())
    err = e.read().decode(errors="replace").rstrip()
    if err:
        print("[stderr]", err)

    if "NET_DONE" not in log:
        print("!! board run did not finish")
        c.close()
        return 1

    print("[down] net_out/ ...")
    sftp = c.open_sftp()
    got = {}
    for name in node_names:
        rp = "%s/net_out/%s.bin" % (REMOTE, name)
        try:
            st = sftp.stat(rp)
        except IOError:
            print("   missing on board:", name)
            continue
        with sftp.open(rp, "rb") as fh:
            got[name] = fh.read()
            lp = os.path.join(LOCAL_OUT, name + ".bin")
        with open(lp, "wb") as fh:
            fh.write(got[name])
    sftp.close()
    c.close()

    # ---- 逐节点比对 ----
    print("\n=== node-by-node comparison (board vs HLS C golden) ===")
    print("%-38s %-5s %10s %8s" % ("node", "kind", "mismatch", "total"))
    fails, checked = 0, 0
    for i, name in enumerate(node_names):
        gp = os.path.join(VEC, "golden", name + ".bin")
        g = open(gp, "rb").read()
        if name not in got:
            print("%-38s %-5s %10s" % (name, "?", "MISSING"))
            fails += 1
            continue
        b = got[name]
        if len(b) != len(g):
            print("%-38s %-5s %10s (len board=%d golden=%d)" % (name, "?", "LEN", len(b), len(g)))
            fails += 1
            continue
        bn = len(g) // 2
        ga = struct.unpack("<%dh" % bn, g)
        ba = struct.unpack("<%dh" % bn, b)
        bad = 0
        first = None
        for k in range(bn):
            if ga[k] != ba[k]:
                if first is None:
                    first = k
                bad += 1
        checked += 1
        fails += bad != 0
        tag = "PASS" if bad == 0 else "FAIL"
        extra = ""
        if bad:
            li = first % 8
            px = first // 8
            extra = "  first: lane %d pixel %d golden=%d board=%d" % (li, px, ga[first], ba[first])
        print("%-38s %-5s %10d %8d  %s%s" % (name, "", bad, bn, tag, extra))
        if i + 1 >= max_node:
            break

    print("\nRESULT: %d/%d nodes bit-exact, %d failed" % (checked - fails, checked, fails))
    return 0 if fails == 0 and checked == len(node_names) else 1


if __name__ == "__main__":
    sys.exit(main())
