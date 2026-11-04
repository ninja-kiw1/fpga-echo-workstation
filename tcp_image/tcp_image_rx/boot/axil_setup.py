"""Upload axil_test.c + the img_proc_axil bitstream to the board and compile.

Board-native gcc (the board has a full toolchain), so no cross-compile needed.
"""
from pathlib import Path
import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"
REMOTE = "/root/tcp_image_rx"
LOCAL_C = str(Path(__file__).resolve().parents[1] / "boot/axil_test.c")
LOCAL_BIN = str(Path(__file__).resolve().parents[1] / "bitstream/img_proc_axil.bin")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=180):
    _i, o, e = cli.exec_command(cmd, timeout=timeout)
    return (o.read().decode("utf-8", "replace").rstrip(),
            e.read().decode("utf-8", "replace").rstrip())


print(run("mkdir -p %s" % REMOTE)[0])

sftp = cli.open_sftp()
sftp.put(LOCAL_C, REMOTE + "/axil_test.c")
print("uploaded axil_test.c")
sftp.put(LOCAL_BIN, "/lib/firmware/img_proc_axil.bin")
st = sftp.stat("/lib/firmware/img_proc_axil.bin")
print("uploaded /lib/firmware/img_proc_axil.bin  %d bytes" % st.st_size)
sftp.close()

print("\n=== compile on board ===")
o, e = run("cd %s && gcc -O2 -Wall -Wextra -o axil_test axil_test.c && echo BUILD_OK && ls -l axil_test" % REMOTE)
print(o)
if e:
    print("[stderr] " + e)

print("\n=== current fpga state ===")
print(run("lsmod | grep -i fpga; cat /sys/class/fpga_manager/fpga0/state")[0])
cli.close()
print("\nSETUP_DONE")
