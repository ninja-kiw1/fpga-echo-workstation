"""Probe the Vitis API to find how to build/get the FSBL + PMUFW ELFs.

Logs every attempt and result to boot/vitis_probe.log; also records the
workspace file tree so we can see where anything is written.
"""
from pathlib import Path
import glob
import os
import time
import traceback

WS = str(Path(__file__).resolve().parents[1] / "vws_d")
LOG = str(Path(__file__).resolve().parents[1] / "boot/vitis_probe.log")
open(LOG, "w", encoding="utf-8").close()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")


def tree(tag):
    log("--- tree %s ---" % tag)
    for p in sorted(glob.glob(WS + "/**/*", recursive=True)):
        if "qemu" in p or "__pycache__" in p or "/.git" in p:
            continue
        n = os.path.basename(p)
        if os.path.isfile(p) and (n.endswith(".elf") or n.endswith(".bif") or n.endswith(".pdi")):
            log("  FILE %s %d" % (p, os.path.getsize(p)))


log("start " + time.ctime())
import vitis
client = vitis.create_client()
client.set_workspace(WS)
log("workspace set")

plat = None
for p in client.list_platform_components():
    log("platform list entry: %r" % (p,))

# try getting the platform as a component
for nm in ("boot_plat",):
    try:
        c = client.get_component(name=nm)
        log("get_component(%s) -> %r" % (nm, c))
    except Exception:
        log("get_component(%s) EXC: %s" % (nm, traceback.format_exc().splitlines()[-1]))

# boot components: try the platform again, no remove
try:
    plat = client.create_platform_component(
        name="boot_plat", hw_design=str(Path(__file__).resolve().parents[1] / "bitstream/boot_design.xsa"),
        os="standalone", cpu="psu_cortexa53_0", is_pmufw_req=True)
    log("(re)create platform -> %r" % (plat,))
except Exception:
    log("recreate platform EXC: %s" % traceback.format_exc().splitlines()[-1])
    # maybe it already exists -> get_sw_platform / get_hw_platform
    for fn in ("get_sw_platform", "get_hw_platform"):
        try:
            r = getattr(client, fn)()
            log("%s -> %r" % (fn, r))
        except Exception:
            log("%s EXC: %s" % (fn, traceback.format_exc().splitlines()[-1]))

for d in ("update_workspace",):
    try:
        r = getattr(client, d)()
        log("%s -> %r" % (d, r))
    except Exception:
        log("%s EXC: %s" % (d, traceback.format_exc().splitlines()[-1]))

tree("after setup")
log("done " + time.ctime())
try:
    vitis.dispose()
except Exception:
    pass
