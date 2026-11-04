"""Build the ZynqMP FSBL and PMUFW from our XSA with the Vitis Python API.

Run in batch mode (NOT the REPL -- create_client() misbehaves there):
    vitis -s vitis_fsbl.py
with PYTHONUTF8=1 (Chinese Windows: create_client/SDT otherwise fails).

Result: FSBL and PMUFW ELFs under the workspace; paths are logged to
vitis/fsbl_gen.log.
"""
from pathlib import Path
import glob
import os
import time
import traceback

WS = str(Path(__file__).resolve().parents[1] / "vws_b")
XSA = str(Path(__file__).resolve().parents[1] / "bitstream/boot_design.xsa")
LOG = str(Path(__file__).resolve().parents[1] / "boot/fsbl_gen.log")

# Vitis must create the workspace itself; a pre-existing plain dir is rejected
# ("cannot recognize the workspace version").
import shutil
if os.path.isdir(WS):
    shutil.rmtree(WS, ignore_errors=True)
open(LOG, "w", encoding="utf-8").close()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")


log("start " + time.ctime())
try:
    import vitis
    log("vitis module: %s" % vitis.__file__)
    client = vitis.create_client()
    log("client ok")
    client.set_workspace(WS)
    log("workspace set: %s" % WS)

    plat = client.create_platform_component(
        name="boot_plat",
        hw_design=XSA,
        os="standalone",
        cpu="psu_cortexa53_0",
        is_pmufw_req=True)
    log("platform created: %r" % (plat,))

    plat.build()
    log("platform build() returned")

    r1 = plat.generate_boot_bsp(target_processor="zynqmp_fsbl")
    log("generate_boot_bsp(fsbl) -> %r" % (r1,))
    try:
        r2 = plat.generate_boot_bsp(target_processor="zynqmp_pmufw")
        log("generate_boot_bsp(pmufw) -> %r" % (r2,))
    except Exception:
        log("pmufw gen EXC:\n" + traceback.format_exc())

    log("workspace tree:")
    for p in sorted(glob.glob(WS + "/**/*", recursive=True)):
        if os.path.isfile(p) and (p.endswith(".elf") or p.endswith(".xpfm")):
            log("   %s  %d" % (p, os.path.getsize(p)))
except Exception:
    log("EXC:\n" + traceback.format_exc())

log("done " + time.ctime())
try:
    vitis.dispose()
except Exception:
    pass
