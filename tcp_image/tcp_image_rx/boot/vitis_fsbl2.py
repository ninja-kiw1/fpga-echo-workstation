"""Re-generate (and thereby build) the platform's boot components (FSBL + PMUFW).

Platform creation only drops the boot-component *sources*; generate_boot_bsp is
what builds them, but it refuses while components already exist, so we remove
them first.
"""
from pathlib import Path
import glob
import os
import shutil
import time
import traceback

WS = str(Path(__file__).resolve().parents[1] / "vws_d")
XSA = str(Path(__file__).resolve().parents[1] / "bitstream/boot_design.xsa")
LOG = str(Path(__file__).resolve().parents[1] / "boot/fsbl_gen.log")

if os.path.isdir(WS):
    shutil.rmtree(WS, ignore_errors=True)
open(LOG, "w", encoding="utf-8").close()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")


def find_elfs(tag):
    hits = glob.glob(WS + "/**/*.elf", recursive=True)
    log("%s: %d elf(s)" % (tag, len(hits)))
    for p in hits:
        log("   %s  %d" % (p, os.path.getsize(p)))
    return hits


log("start " + time.ctime())
try:
    import vitis
    client = vitis.create_client()
    client.set_workspace(WS)
    log("workspace " + WS)

    plat = client.create_platform_component(
        name="boot_plat", hw_design=XSA, os="standalone",
        cpu="psu_cortexa53_0", is_pmufw_req=True)
    log("platform created")
    plat.build()
    log("platform build done")
    find_elfs("after platform build")

    log("remove_boot_bsp -> %r" % (plat.remove_boot_bsp(),))
    log("generate_boot_bsp(a53) -> %r" % (plat.generate_boot_bsp(target_processor="psu_cortexa53_0"),))
    find_elfs("after fsbl gen")
    try:
        log("generate_boot_bsp(pmu) -> %r" % (plat.generate_boot_bsp(target_processor="psu_pmu_0"),))
    except Exception:
        log("pmufw EXC:\n" + traceback.format_exc())
    find_elfs("after pmufw gen")

    log("export dir:")
    for p in glob.glob(WS + "/boot_plat/export/**/*", recursive=True):
        log("   %s" % p)
except Exception:
    log("EXC:\n" + traceback.format_exc())

log("done " + time.ctime())
try:
    vitis.dispose()
except Exception:
    pass
