"""Load the img_proc_axil bitstream on the board and run the register test.

Output is unbuffered (stdbuf -o0) so that if the PL ever stalls the AXI read
we still see how far it got.
"""
import sys

import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"
REMOTE = "/root/tcp_image_rx"

mode = sys.argv[1] if len(sys.argv) > 1 else "0"
thr = sys.argv[2] if len(sys.argv) > 2 else "128"
pattern = sys.argv[3] if len(sys.argv) > 3 else "ramp"
npush = sys.argv[4] if len(sys.argv) > 4 else ""

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=180):
    _i, o, e = cli.exec_command(cmd, timeout=timeout)
    try:
        return (o.read().decode("utf-8", "replace").rstrip(),
                e.read().decode("utf-8", "replace").rstrip())
    except Exception as ex:
        return ("(read timeout: %r)" % (ex,), "")


print("=== load bitstream ===")
print(run("rmmod fpgadrv 2>&1 || echo '(fpgadrv not loaded)'")[0])
print(run("echo img_proc_axil.bin > /sys/class/fpga_manager/fpga0/firmware 2>&1; echo rc=$?")[0])
print("state:", run("cat /sys/class/fpga_manager/fpga0/state")[0])

print("\n=== axil_test mode=%s thr=%s pattern=%s npush=%s ===" % (mode, thr, pattern, npush or "all"))
o, e = run("cd %s && stdbuf -o0 ./axil_test 0xA0000000 %s %s %s %s" % (REMOTE, mode, thr, pattern, npush),
           timeout=150)
print(o)
if e:
    print("[stderr] " + e)

print("\n=== board still alive? ===")
print(run("echo alive; cat /proc/uptime")[0])
cli.close()
print("AXIL_RUN_DONE")
