"""Inventory the FZ3B board over SSH (paramiko).  Writes a report next to this file.

Used by the boot.bin rebuild work: we need to know what the vendor SD image
carries, whether ATF/U-Boot can be extracted, and how the boot partition is
laid out.  Run with Vivado's bundled python (has paramiko in --user).
"""
from pathlib import Path
import sys

try:
    import paramiko
except Exception as e:  # pragma: no cover
    print("NO_PARAMIKO: %s" % e)
    sys.exit(2)

HOST = "192.168.1.254"
USER = "root"
PWD = "root"
OUT = str(Path(__file__).resolve().parents[1] / "boot/inventory.txt")

CMDS = [
    ("uname",            "uname -a"),
    ("cmdline",          "cat /proc/cmdline"),
    ("os-release",       "head -3 /etc/os-release 2>/dev/null"),
    ("partitions",       "cat /proc/partitions"),
    ("lsblk",            "lsblk 2>/dev/null || echo NO_LSBLK"),
    ("mounts",           "cat /proc/mounts"),
    ("df",               "df -h"),
    ("root-ls",          "ls -la /"),
    ("boot-ls",          "ls -la /boot 2>/dev/null"),
    ("bootsub-ls",       "ls -la /boot/* 2>/dev/null | head -60"),
    ("media-ls",         "ls -la /media /run/media 2>/dev/null; ls -la /mnt 2>/dev/null"),
    ("find-bootbin",     "find / -maxdepth 5 -iname 'boot.bin*' 2>/dev/null"),
    ("find-bl",          "find / -maxdepth 6 \\( -iname 'bl31*' -o -iname 'u-boot*' -o -iname 'fsbl*' -o -iname 'pmufw*' \\) 2>/dev/null | head -40"),
    ("find-image",       "find / -maxdepth 5 \\( -iname 'image.ub' -o -iname 'Image' -o -iname '*.dtb' \\) 2>/dev/null | head -40"),
    ("dmesg-boot",       "dmesg 2>/dev/null | grep -iE 'fsbl|xilinx|fpga|zynq|bl31|trusted|u-boot|mmcblk|pl_|bitstream' | head -40"),
    ("fdisk",            "fdisk -l 2>/dev/null | head -50"),
    ("which",            "which mkfs.vfat mount fdisk dd 2>/dev/null"),
    ("kernel",           "ls -la /lib/modules 2>/dev/null"),
    ("fpga-mgr",         "ls -la /sys/class/fpga_manager/ 2>/dev/null; ls /lib/firmware 2>/dev/null | head"),
    ("mtd",              "cat /proc/mtd 2>/dev/null; ls /dev/mmcblk* 2>/dev/null"),
]

lines = []


def add(s=""):
    # ascii-only safety for tcl consumption is not needed (we write a file), but
    # keep it simple and readable.
    lines.append(s)


cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    cli.connect(HOST, username=USER, password=PWD, timeout=20,
                allow_agent=False, look_for_keys=False)
except Exception as e:
    print("CONNECT_FAIL: %s" % e)
    sys.exit(3)

add("### FZ3B BOARD INVENTORY ###")
for name, cmd in CMDS:
    add("")
    add("========== %s ==========" % name)
    add("$ %s" % cmd)
    try:
        _in, out, err = cli.exec_command(cmd, timeout=60)
        o = out.read().decode("utf-8", errors="replace").rstrip()
        e = err.read().decode("utf-8", errors="replace").rstrip()
        add(o if o else "(empty)")
        if e:
            add("[stderr] " + e)
    except Exception as ex:
        add("(timeout/err: %s)" % ex)

cli.close()

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("OK wrote %s (%d lines)" % (OUT, len(lines)))
