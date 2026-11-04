"""Build the FSBL/PMUFW components by getting them via their workspace location.

client.get_component(name=...) actually takes a *location*; a bare name fails
with "Invalid project location".  Once we hold the Component object we can call
build()/generate_build_files().
"""
from pathlib import Path
import glob
import os
import time
import traceback

WS = str(Path(__file__).resolve().parents[1] / "vws_d")
LOG = str(Path(__file__).resolve().parents[1] / "boot/fsbl_gen.log")
open(LOG, "w", encoding="utf-8").close()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")


def elfs(tag):
    hits = [p for p in glob.glob(WS + "/**/*.elf", recursive=True) if "qemu" not in p]
    log("%s: %d non-qemu elf(s)" % (tag, len(hits)))
    for p in hits:
        log("   %s  %d" % (p, os.path.getsize(p)))
    return hits


log("start " + time.ctime())
try:
    import vitis
    client = vitis.create_client()
    client.set_workspace(WS)
    log("workspace ok")

    for loc in (WS + "/boot_plat",
                WS + "/boot_plat/zynqmp_fsbl",
                WS + "/boot_plat/zynqmp_pmufw"):
        try:
            c = client.get_component(name=loc)
            log("get_component(%s) -> %r" % (loc, c))
            try:
                log("   build() -> %r" % (c.build(),))
            except Exception:
                log("   build EXC: %s" % traceback.format_exc().splitlines()[-1])
        except Exception:
            log("get_component(%s) EXC: %s" % (loc, traceback.format_exc().splitlines()[-1]))

    elfs("after component builds")
except Exception:
    log("OUTER EXC:\n" + traceback.format_exc())
log("done " + time.ctime())
try:
    vitis.dispose()
except Exception:
    pass
