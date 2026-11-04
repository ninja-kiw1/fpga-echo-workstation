"""Hand-compile the generated ZynqMP FSBL into fsbl.elf.

Flags come verbatim from the component's compile_commands.json:
    <Vitis>/gnu/aarch64/nt/aarch64-none/bin/aarch64-none-elf-gcc.exe
    -DSDT -specs=<bsp>/Xilinx.spec -Os -flto -ffat-lto-objects -DARMA53_64
The BSP libraries are the ones ninja just produced for this component
(build_fsbl_bsp.bat); driver headers come from the standalone BSP via CPATH.
"""
from pathlib import Path
from _local_paths import tool_path
import glob
import os
import subprocess
import time

GCC = tool_path("Vitis/gnu/aarch64/nt/aarch64-none/bin/aarch64-none-elf-gcc.exe")
VWS = str(Path(__file__).resolve().parents[1] / "vws_d")
FSBL = VWS + "/boot_plat/zynqmp_fsbl"
BSP = FSBL + "/zynqmp_fsbl_bsp"
GEN = BSP + "/libsrc/build_configs/gen_bsp"
STBSP = VWS + "/boot_plat/psu_cortexa53_0/standalone_psu_cortexa53_0/bsp"
OUTDIR = str(Path(__file__).resolve().parents[1] / "boot/parts")
BUILD = str(Path(__file__).resolve().parents[1] / "boot/fsbl_build")
LOG = str(Path(__file__).resolve().parents[1] / "boot/manual_fsbl.log")

os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(BUILD, exist_ok=True)
for old in glob.glob(BUILD + "/*.o"):
    os.remove(old)
open(LOG, "w", encoding="utf-8").close()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")
    print(m)


log("start " + time.ctime())

INCS = ["-I" + BSP + "/include", "-I" + GEN + "/include",
        "-I" + STBSP + "/include", "-I" + STBSP + "/libsrc/build_configs/gen_bsp/include",
        "-I" + FSBL]
CFLAGS = ["-DSDT", "-specs=" + BSP + "/Xilinx.spec",
          "-Os", "-flto", "-ffat-lto-objects", "-DARMA53_64",
          "-ffunction-sections", "-fdata-sections"] + INCS

srcs = sorted(glob.glob(FSBL + "/*.c") + glob.glob(FSBL + "/*.S"))
log("sources: %d" % len(srcs))

objs, fails = [], 0
for s in srcs:
    o = os.path.join(BUILD, os.path.basename(s) + ".o")
    r = subprocess.run([GCC] + CFLAGS + ["-c", s, "-o", o], capture_output=True, text=True)
    if r.returncode != 0:
        fails += 1
        log("COMPILE FAIL %s\n%s\n%s" % (os.path.basename(s), r.stdout[-1200:], r.stderr[-1200:]))
    else:
        objs.append(o)
log("compiled %d ok, %d failed" % (len(objs), fails))
if fails:
    raise SystemExit(1)

elf = OUTDIR + "/fsbl.elf"
LIBDIRS = ["-L" + STBSP + "/lib",
           "-L" + GEN + "/libsrc/standalone/src",
           "-L" + GEN + "/libsrc/xiltimer/src",
           "-L" + GEN + "/libsrc/xilffs/src",
           "-L" + GEN + "/libsrc/xilsecure/src",
           "-L" + GEN + "/libsrc/xilpm/src"]
link = ([GCC, "-o", elf] + objs
        + ["-T" + FSBL + "/lscript_a53.ld", "-specs=" + BSP + "/Xilinx.spec",
           "-Os", "-flto", "-ffat-lto-objects"]
        + LIBDIRS
        + ["-Wl,--start-group", "-lxil", "-lxilstandalone", "-lxiltimer", "-lxilffs",
           "-lxilsecure", "-lxilpm", "-lgcc", "-lc", "-Wl,--end-group",
           "-Wl,--gc-sections", "-Wl,--build-id=none"])
log("linking...")
r = subprocess.run(link, capture_output=True, text=True)
log("link rc=%d" % r.returncode)
if r.stdout:
    log("stdout:\n" + r.stdout[-2500:])
if r.stderr:
    log("stderr:\n" + r.stderr[-2500:])
log("fsbl.elf exists=%s size=%s" % (os.path.exists(elf), os.path.getsize(elf) if os.path.exists(elf) else 0))
log("done " + time.ctime())
