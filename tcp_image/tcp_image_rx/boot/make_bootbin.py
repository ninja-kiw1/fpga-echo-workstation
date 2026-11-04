"""Assemble the new boot.bin with bootgen from our own FSBL/PMUFW/bitstream plus
the vendor's ATF (bl31) and U-Boot extracted from the original image.

Inputs (must all exist):
  parts/fsbl.elf        - built by Vitis from our XSA  (PS config w/ S_AXI_HP0_FPD)
  parts/pmufw.elf       - built by Vitis
  parts/boot_design.bin - our PL bitstream (bootgen .bin form)
  parts/bl31.elf        - extracted from the vendor boot.bin (wrapper)
  parts/u-boot.elf      - extracted from the vendor boot.bin (wrapper)

Writes parts/boot.bif, parts/boot.bin and a readable dump parts/boot.bin.read.txt.
"""
from pathlib import Path
from _local_paths import tool_path
import os
import subprocess
import sys

BOOTGEN = tool_path("Vitis/bin/bootgen.bat")
PARTS = str(Path(__file__).resolve().parents[1] / "boot/parts")

NEED = {
    "fsbl": os.path.join(PARTS, "fsbl.elf"),
    "pmufw": os.path.join(PARTS, "pmufw.elf"),
    "bit": os.path.join(PARTS, "boot_design.bin"),
    "bl31": os.path.join(PARTS, "bl31.elf"),
    "uboot": os.path.join(PARTS, "u-boot.elf"),
}

missing = [k for k, v in NEED.items() if not os.path.exists(v)]
if missing:
    print("MISSING inputs: %s" % missing)
    for k in missing:
        print("   %-6s -> %s" % (k, NEED[k]))
    sys.exit(2)

# normalise + forward slashes for bootgen (it dislikes backslashes in some spots)
P = {k: v.replace("\\", "/") for k, v in NEED.items()}

bif = (
    "the_ROM_image:\n"
    "{\n"
    "\t[fsbl_config] a53_x64\n"
    "\t[bootloader] " + P["fsbl"] + "\n"
    "\t[pmufw_image] " + P["pmufw"] + "\n"
    "\t[destination_device=pl] " + P["bit"] + "\n"
    "\t[destination_cpu=a53-0, exception_level=el-3] " + P["bl31"] + "\n"
    "\t[destination_cpu=a53-0, exception_level=el-2] " + P["uboot"] + "\n"
    "}\n"
)

bifpath = os.path.join(PARTS, "boot.bif")
open(bifpath, "w").write(bif)
print("wrote %s" % bifpath)
print(bif)

out = os.path.join(PARTS, "boot.bin")
cmd = [BOOTGEN, "-arch", "zynqmp", "-image", bifpath.replace("\\", "/"),
       "-o", out.replace("\\", "/"), "-w", "on"]
print("running: %s" % " ".join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout)
print(r.stderr)
if r.returncode != 0 or not os.path.exists(out):
    print("BOOTGEN FAILED")
    sys.exit(1)

print("boot.bin = %d bytes" % os.path.getsize(out))

# verification dump
r2 = subprocess.run([BOOTGEN, "-arch", "zynqmp", "-read", out.replace("\\", "/")],
                    capture_output=True, text=True)
txt = r2.stdout
open(os.path.join(PARTS, "boot.bin.read.txt"), "w").write(txt)
for l in txt.split("\n"):
    s = l.strip()
    if any(k in s for k in ("IMAGE HEADER (", "PARTITION HEADER TABLE (", "load_addr_lo",
                            "exec_addr_lo", "total_length", "partition_offset",
                            "dest_device", "core [", "trustzone")):
        print("   " + s)
print("MAKE_BOOTBIN_DONE")
