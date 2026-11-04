"""Reboot the FZ3B so it boots the freshly written boot.bin."""
import time

import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("192.168.1.254", username="root", password="root", timeout=20,
            allow_agent=False, look_for_keys=False)
# fire and forget: don't read stdout, the reboot kills the channel
cli.exec_command("sync; (sleep 3; reboot) >/dev/null 2>&1 &")
print("reboot issued; waiting 8s before closing channel")
time.sleep(8)
try:
    cli.close()
except Exception:
    pass
print("REBOOT_ISSUED")
