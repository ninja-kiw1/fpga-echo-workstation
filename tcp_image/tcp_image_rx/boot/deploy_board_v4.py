"""Stage, back up, and activate the v4 board server; never reload the FPGA.

Run prepare, then activate. rollback restores the exact previous executable.
Paramiko is installed in tcp_image/.board_tools, isolated from system Python.
"""
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

RX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RX.parent / ".board_tools"))
import paramiko

OUT = RX.parent / "tcp_image_ui/board_check_v4"
MANIFEST = OUT / "deployment.json"
REMOTE = "/root/tcp_image_rx"


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    OUT.mkdir(exist_ok=True)
    c = paramiko.SSHClient()
    known = OUT / "known_hosts"
    if known.exists():
        c.load_host_keys(str(known))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10,
              allow_agent=False, look_for_keys=False)
    c.save_host_keys(str(known))

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
        if action in ("prepare", "rebuild"):
            if action == "prepare" and MANIFEST.exists():
                raise RuntimeError("Deployment manifest already exists; inspect it before staging again")
            previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if action == "rebuild" else None
            tag = "v4_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            release = REMOTE + "/releases/" + tag
            backup = previous["backup"] if previous else release + "/previous"
            before = previous["old_sha256"] if previous else run("sha256sum " + REMOTE + "/tcp_image_server").split()[0]
            firmware = run("sha256sum /lib/firmware/ai_sys.bin").split()[0]
            run("mkdir -p " + release + "/src " + release + "/test " + backup)
            if not previous:
                run("cp -a " + REMOTE + "/tcp_image_server " + backup + "/tcp_image_server && "
                    "cp -a /etc/systemd/system/tcp-image-server.service " + backup + "/")
                run("systemctl cat tcp-image-server > " + backup + "/effective.service")
            else:
                (OUT / ("deployment_" + Path(previous["release"]).name + ".json")).write_text(
                    json.dumps(previous, indent=2), encoding="utf-8")
            manifest = {"release": release, "backup": backup, "old_sha256": before,
                        "firmware_sha256": firmware, "state": "backed_up", "sources": {}}
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with c.open_sftp() as s:
                s.get(backup + "/tcp_image_server", str(OUT / "tcp_image_server.previous"))
                s.get(backup + "/effective.service", str(OUT / "previous.service"))
                paths = list((RX / "src").glob("*.[ch]")) + [RX / "makefile", RX / "test/ai_view_count_test.c"]
                for path in paths:
                    relative = path.relative_to(RX).as_posix()
                    s.put(str(path), release + "/" + relative)
                    manifest["sources"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            run("cd " + release + " && make CC=gcc -j2", 180)
            run("cd " + release + " && gcc -O2 -Wall -Wextra -Werror -std=c99 -Isrc "
                "test/ai_view_count_test.c src/ai_view.c -o ai_view_count_test && ./ai_view_count_test")
            manifest["new_sha256"] = run("sha256sum " + release + "/tcp_image_server").split()[0]
            manifest["state"] = "built"
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        elif action in ("activate", "rollback"):
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if action == "activate" and manifest["state"] != "built":
                raise RuntimeError("Expected a successfully built release")
            source = (manifest["release"] if action == "activate" else manifest["backup"]) + "/tcp_image_server"
            run("systemctl stop tcp-image-server")
            try:
                run("install -m 0755 " + shlex.quote(source) + " " + REMOTE + "/tcp_image_server.next && "
                    "mv " + REMOTE + "/tcp_image_server.next " + REMOTE + "/tcp_image_server")
                run("systemctl start tcp-image-server && systemctl is-active tcp-image-server")
            except Exception:
                run("install -m 0755 " + manifest["backup"] + "/tcp_image_server " + REMOTE + "/tcp_image_server && systemctl start tcp-image-server")
                raise
            actual = run("sha256sum " + REMOTE + "/tcp_image_server").split()[0]
            expected = manifest["new_sha256"] if action == "activate" else manifest["old_sha256"]
            if actual != expected:
                raise RuntimeError("Installed executable checksum mismatch")
            manifest["state"] = "active" if action == "activate" else "rolled_back"
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        elif action == "sync-sources":
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if manifest["state"] != "active":
                raise RuntimeError("Only sync the active verified release")
            archive = manifest["backup"] + "/source_previous.tar.gz"
            run("test -f " + archive + " || tar -C " + REMOTE + " -czf " + archive + " src makefile")
            run("cp -a " + manifest["release"] + "/src/. " + REMOTE + "/src/ && "
                "cp -a " + manifest["release"] + "/makefile " + REMOTE + "/makefile")
            with c.open_sftp() as s:
                s.get(archive, str(OUT / "source_previous.tar.gz"))
                for relative, expected in manifest["sources"].items():
                    if relative.startswith("test/"):
                        continue
                    with s.open(REMOTE + "/" + relative, "rb") as f:
                        actual = hashlib.sha256(f.read()).hexdigest()
                    if actual != expected:
                        raise RuntimeError("Source checksum mismatch: " + relative)
            manifest["root_sources_synced"] = True
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print("SOURCE_SYNC_PASS", flush=True)
        elif action == "status":
            run("systemctl is-active tcp-image-server ai-sys-bitstream plbram plbdma; "
                "sha256sum " + REMOTE + "/tcp_image_server /lib/firmware/ai_sys.bin; "
                "ss -ltnp | grep ':5060'")
        else:
            raise ValueError("Unknown action: " + action)
    finally:
        c.close()


if __name__ == "__main__":
    main()
