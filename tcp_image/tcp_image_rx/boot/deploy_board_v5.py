"""Stage, back up, activate (and roll back) the v5 board server: model library.

Same procedure as deploy_board_v4.py, with its own manifest in
tcp_image_ui/board_check_v5/. Never reloads the FPGA.

  prepare   back up the running executable + unit, upload src/ + makefile +
            test/ai_view_count_test.c to /root/tcp_image_rx/releases/v5_<utc>/,
            build natively, run the unit test (nothing is switched yet)
  activate  stop the service, install the built executable, start, verify hash
  rollback  restore the executable that was running before this release and start
  status    service / hash summary

Since v5.17 the release also carries the unit drop-in boot/tcp-image-server.service.d/10-nice.conf
(Nice=-20): prepare records whether the board had one (previous/dropin.state), activate installs it,
rollback puts the previous state back (removes it if there was none). Manifests from older
releases have no "dropin" key; their rollback leaves the drop-in alone.

/root/tcp_image_rx/src/ is NOT touched: Fz3b_BoardCheck compiles unet_ai.c from
there and would also need src/unet_models.c in its makefile (see the v5 notes).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe deploy_board_v5.py prepare|activate|rollback|status

prepare --pin=src/x.c=D:/path/x.c builds the release with that copy of src/x.c instead of the
working tree's (v5.17: J1 only, while job_simpson.c carried another session's undeployed change);
the manifest lists it under "pinned" and its "sources" hash is the pinned copy's.
prepare --exclude=src/x.c leaves that working-tree file out of the release (v5.18: part A only, while
src/fundus_cdr.c/.h of part D sat undeployed in the tree; the makefile builds every src/*.c); the manifest lists it
under "excluded".
"""
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

RX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RX.parent / ".board_tools"))
import paramiko  # noqa: E402

OUT = RX.parent / "tcp_image_ui/board_check_v5"
MANIFEST = OUT / "deployment.json"
REMOTE = "/root/tcp_image_rx"
KNOWN = RX.parent / "tcp_image_ui/board_check_v4/known_hosts"
DROPIN = RX / "boot/tcp-image-server.service.d/10-nice.conf"
DROPIN_REMOTE = "/etc/systemd/system/tcp-image-server.service.d/10-nice.conf"


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    pins, excludes = {}, set()
    for arg in sys.argv[2:]:
        if arg.startswith("--exclude="):
            excludes.add(arg[10:])
            if not (RX / arg[10:]).is_file():
                raise FileNotFoundError(arg[10:])
            continue
        if not arg.startswith("--pin=") or "=" not in arg[6:]:
            raise ValueError("Unknown argument: " + arg)
        relative, local = arg[6:].split("=", 1)
        pins[relative] = Path(local)
        if not pins[relative].is_file():
            raise FileNotFoundError(local)
    OUT.mkdir(exist_ok=True)
    c = paramiko.SSHClient()
    if KNOWN.exists():
        c.load_host_keys(str(KNOWN))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10,
              allow_agent=False, look_for_keys=False)

    def run(command, timeout=60):
        print("$", command, flush=True)
        _, out, _ = c.exec_command(command + " 2>&1", timeout=timeout)
        value = out.read().decode("utf-8", "replace")
        status = out.channel.recv_exit_status()
        print(value, end="", flush=True)
        with (OUT / ("deploy_" + action + ".log")).open("a", encoding="utf-8") as f:
            f.write("$ " + command + "\n" + value + "\nexit=" + str(status) + "\n")
        if status:
            raise RuntimeError("remote exit %d: %s" % (status, command))
        return value

    try:
        if action == "prepare":
            # Every release backs up whatever is running right now, so rollback goes
            # one release back (the first v5 release backed up the v4 server).
            if MANIFEST.exists():
                previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
                (OUT / ("deployment_" + Path(previous["release"]).name + ".json")).write_text(
                    json.dumps(previous, indent=2), encoding="utf-8")
            tag = "v5_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            release = REMOTE + "/releases/" + tag
            backup = release + "/previous"
            before = run("sha256sum " + REMOTE + "/tcp_image_server").split()[0]
            firmware = run("sha256sum /lib/firmware/ai_sys.bin").split()[0]
            run("mkdir -p " + release + "/src " + release + "/test " + backup)
            run("test -f " + backup + "/tcp_image_server || (cp -a " + REMOTE + "/tcp_image_server " + backup +
                "/tcp_image_server && cp -a /etc/systemd/system/tcp-image-server.service " + backup +
                "/ && systemctl cat tcp-image-server > " + backup + "/effective.service)")
            had = run("test -f " + DROPIN_REMOTE + " && sha256sum " + DROPIN_REMOTE + " || true").split()
            run("test -f " + backup + "/dropin.state || (if test -f " + DROPIN_REMOTE + "; then cp -a " +
                DROPIN_REMOTE + " " + backup + "/10-nice.conf; echo present; else echo absent; fi > " +
                backup + "/dropin.state)")
            manifest = {"release": release, "backup": backup, "old_sha256": before,
                        "firmware_sha256": firmware, "state": "backed_up", "sources": {},
                        "dropin": {"remote": DROPIN_REMOTE, "old_sha256": had[0] if had else None,
                                   "new_sha256": hashlib.sha256(DROPIN.read_bytes()).hexdigest()}}
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with c.open_sftp() as s:
                s.get(backup + "/tcp_image_server", str(OUT / ("tcp_image_server.before_" + tag)))
                paths = list((RX / "src").glob("*.[ch]")) + [RX / "makefile", RX / "test/ai_view_count_test.c"]
                s.put(str(DROPIN), release + "/10-nice.conf")
                for path in paths:
                    relative = path.relative_to(RX).as_posix()
                    if relative in excludes:      # --exclude: not part of this release
                        manifest.setdefault("excluded", []).append(relative)
                        continue
                    if relative in pins:          # --pin: build this file from another copy
                        path = pins.pop(relative)
                        manifest.setdefault("pinned", {})[relative] = str(path)
                    s.put(str(path), release + "/" + relative)
                    manifest["sources"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                if pins:
                    raise RuntimeError("--pin paths not in the release: " + ", ".join(pins))
            run("cd " + release + " && make CC=gcc -j2", 300)
            run("cd " + release + " && gcc -O2 -Wall -Wextra -Werror -std=c99 -Isrc "
                "test/ai_view_count_test.c src/ai_view.c -o ai_view_count_test && ./ai_view_count_test")
            run("cd " + release + " && ./tcp_image_server --help | grep -A1 -- '--model'")
            manifest["new_sha256"] = run("sha256sum " + release + "/tcp_image_server").split()[0]
            manifest["state"] = "built"
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print("V5_PREPARE_PASS", flush=True)
        elif action in ("activate", "rollback"):
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if action == "activate" and manifest["state"] != "built":
                raise RuntimeError("Expected a successfully built release")
            source = (manifest["release"] if action == "activate" else manifest["backup"]) + "/tcp_image_server"
            dropin = manifest.get("dropin")

            def set_dropin(which):
                # which = "new": the release's drop-in; "old": whatever the board had before prepare
                if dropin is None:
                    return
                if which == "new":
                    run("mkdir -p $(dirname " + DROPIN_REMOTE + ") && install -m 0644 " + manifest["release"] +
                        "/10-nice.conf " + DROPIN_REMOTE)
                elif run("cat " + manifest["backup"] + "/dropin.state").strip() == "present":
                    run("install -m 0644 " + manifest["backup"] + "/10-nice.conf " + DROPIN_REMOTE)
                else:
                    run("rm -f " + DROPIN_REMOTE)
                run("systemctl daemon-reload")

            run("systemctl stop tcp-image-server")
            try:
                set_dropin("new" if action == "activate" else "old")
                run("install -m 0755 " + shlex.quote(source) + " " + REMOTE + "/tcp_image_server.next && "
                    "mv " + REMOTE + "/tcp_image_server.next " + REMOTE + "/tcp_image_server")
                run("systemctl start tcp-image-server && sleep 2 && systemctl is-active tcp-image-server")
            except Exception:
                set_dropin("old")
                run("install -m 0755 " + manifest["backup"] + "/tcp_image_server " + REMOTE +
                    "/tcp_image_server && systemctl start tcp-image-server")
                raise
            actual = run("sha256sum " + REMOTE + "/tcp_image_server").split()[0]
            expected = manifest["new_sha256"] if action == "activate" else manifest["old_sha256"]
            if actual != expected:
                raise RuntimeError("Installed executable checksum mismatch")
            if dropin is not None:
                nice = run("systemctl show -p Nice tcp-image-server").strip()
                run("ps -o pid,ni,comm -C tcp_image_serve")
                if action == "activate" and nice != "Nice=-20":
                    raise RuntimeError("Drop-in not effective: " + nice)
            run("journalctl -u tcp-image-server -n 12 --no-pager | grep -E 'cfg\\]|error|fail|J1' || true")
            manifest["state"] = "active" if action == "activate" else "rolled_back"
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print("V5_%s_PASS" % action.upper(), flush=True)
        elif action == "status":
            run("systemctl is-active tcp-image-server tcp-image-server-skin ai-sys-bitstream plbram plbdma; "
                "sha256sum " + REMOTE + "/tcp_image_server /lib/firmware/ai_sys.bin; "
                "ss -ltnp | grep ':5060'")
        else:
            raise ValueError("Unknown action: " + action)
    finally:
        c.close()


if __name__ == "__main__":
    main()
