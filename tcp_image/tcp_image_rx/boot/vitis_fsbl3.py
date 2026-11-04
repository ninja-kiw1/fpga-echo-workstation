"""Reopen the Vitis workspace, list components, and build FSBL/PMUFW explicitly."""
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


log("start " + time.ctime())
try:
    import vitis
    client = vitis.create_client()
    client.set_workspace(WS)
    log("workspace reopened")

    comps = client.list_components()
    log("list_components -> %r" % (comps,))

    plats = client.list_platform_components()
    log("list_platform_components -> %r" % (plats,))

    for nm in ("zynqmp_fsbl", "zynqmp_pmufw"):
        try:
            c = client.get_component(name=nm)
            log("component %s -> %r" % (nm, c))
            c.build()
            log("built %s" % nm)
        except Exception:
            log("%s EXC:\n%s" % (nm, traceback.format_exc()))

    for p in sorted(glob.glob(WS + "/**/*.elf", recursive=True)):
        if "qemu" not in p:
            log("ELF %s %d" % (p, os.path.getsize(p)))
except Exception:
    log("EXC:\n" + traceback.format_exc())
log("done " + time.ctime())
try:
    vitis.dispose()
except Exception:
    pass
