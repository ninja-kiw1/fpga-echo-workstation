"""Hand-compile the generated PMU firmware (PMUFW) into pmufw.elf.

MicroBlaze build, flags from the component's compile_commands.json:
    <Vitis>/gnu/microblaze/nt/bin/mb-gcc.exe
    -O2 -mlittle-endian -mxl-barrel-shift -mxl-pattern-compare -mxl-reorder
    -mxl-soft-mul -mxl-soft-div -g -feliminate-unused-debug-types
    -Os -flto -ffat-lto-objects -DPSU_PMU=1U -mcpu=v9.2 -DSDT
    -specs=<bsp>/Xilinx.spec -I<bsp>/include
BSP archives come from the ninja build of this component's BSP.
"""
from pathlib import Path
from _local_paths import tool_path
import glob
import os
import subprocess
import time

GCC = tool_path("Vitis/gnu/microblaze/nt/bin/mb-gcc.exe")
VWS = str(Path(__file__).resolve().parents[1] / "vws_d")
PMU = VWS + "/boot_plat/zynqmp_pmufw"
BSP = PMU + "/zynqmp_pmufw_bsp"
GEN = BSP + "/libsrc/build_configs/gen_bsp"
STBSP = VWS + "/boot_plat/psu_cortexa53_0/standalone_psu_cortexa53_0/bsp"
OUTDIR = str(Path(__file__).resolve().parents[1] / "boot/parts")
BUILD = str(Path(__file__).resolve().parents[1] / "boot/pmufw_build")
LOG = str(Path(__file__).resolve().parents[1] / "boot/manual_pmufw.log")

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
log("mb-gcc exists: %s" % os.path.exists(GCC))
log("lscript: %s" % os.path.exists(PMU + "/lscript.ld"))

INCS = ["-I" + BSP + "/include", "-I" + GEN + "/include",
        "-I" + STBSP + "/include", "-I" + STBSP + "/libsrc/build_configs/gen_bsp/include",
        "-I" + PMU]
BASE = ["-O2", "-mlittle-endian", "-mxl-barrel-shift", "-mxl-pattern-compare",
        "-mxl-reorder", "-mxl-soft-mul", "-mxl-soft-div",
        "-g", "-feliminate-unused-debug-types", "-Os", "-flto", "-ffat-lto-objects",
        "-DPSU_PMU=1U", "-mcpu=v9.2", "-DSDT",
        "-specs=" + BSP + "/Xilinx.spec",
        "-ffunction-sections", "-fdata-sections"]
CFLAGS = BASE + INCS

srcs = sorted(glob.glob(PMU + "/*.c") + glob.glob(PMU + "/*.S"))
# The PMUFW's collected BSP archives only cover standalone/xiltimer/xilffs/
# xilsecure/xilfpga/xilskey; the IPI driver it actually calls is shipped as
# sources but never built, so add those directories here.
EXTRA_DIRS = ["ipipsu/src", "csudma/src", "uartps/src"]
for d in EXTRA_DIRS:
    srcs += sorted(glob.glob(BSP + "/libsrc/" + d + "/*.c"))
# local shims (Xil_DCacheInvalidateRange / FlushRange are ARM-only APIs)
srcs.append(str(Path(__file__).resolve().parents[1] / "boot/pmufw_stubs.c"))
# xil_util.c / xil_sutil.c (Xil_WaitForEvent) are not in the collected archive
EXTRA_FILES = ["standalone/src/common/xil_util.c", "standalone/src/common/xil_sutil.c",
               "standalone/src/common/outbyte.c", "standalone/src/common/xil_mem.c",
               "standalone/src/microblaze/xil_cache.c", "standalone/src/microblaze/xio.c"]
for f in EXTRA_FILES:
    p = BSP + "/libsrc/" + f
    if os.path.exists(p):
        srcs.append(p)
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

# all BSP archives built by ninja for this component
libs = sorted(p for p in glob.glob(GEN + "/**/*.a", recursive=True))
log("bsp archives: %d" % len(libs))

# The MicroBlaze toolchain links -lxil implicitly, but Vitis never emits a
# libxil.a for this component, so merge the BSP archives into one.
AR = tool_path("Vitis/gnu/microblaze/nt/bin/mb-ar.exe")
MERGE = BUILD + "/xilmerge"
os.makedirs(MERGE, exist_ok=True)
for old in glob.glob(MERGE + "/*"):
    os.remove(old)
for a in libs:
    r = subprocess.run([AR, "x", a.replace("\\", "/")], cwd=MERGE,
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("ar x failed for %s: %s" % (a, r.stderr[-400:]))
members = glob.glob(MERGE + "/*.o")
merged = BUILD + "/libxil.a"
if os.path.exists(merged):
    os.remove(merged)
r = subprocess.run([AR, "rcs", merged] + members, capture_output=True, text=True)
log("merged libxil.a: rc=%d members=%d size=%s" % (
    r.returncode, len(members), os.path.getsize(merged) if os.path.exists(merged) else 0))

elf = OUTDIR + "/pmufw.elf"
link = ([GCC, "-o", elf] + objs + libs
        + ["-T" + PMU + "/lscript.ld"] + BASE
        + ["-L" + BUILD, "-nostartfiles",
           "-Wl,--start-group", "-lgcc", "-lc", "-Wl,--end-group",
           "-Wl,--allow-multiple-definition",
           "-Wl,--gc-sections", "-Wl,--build-id=none"])
log("linking...")
r = subprocess.run(link, capture_output=True, text=True)
log("link rc=%d" % r.returncode)
if r.stdout:
    log("stdout:\n" + r.stdout[-2500:])
if r.stderr:
    log("stderr:\n" + r.stderr[-2500:])
log("pmufw.elf exists=%s size=%s" % (os.path.exists(elf), os.path.getsize(elf) if os.path.exists(elf) else 0))
log("done " + time.ctime())
