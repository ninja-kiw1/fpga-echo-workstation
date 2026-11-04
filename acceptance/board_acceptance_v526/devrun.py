r"""Server v5.26 CANDIDATE (echo_ef mode 3: RQ01 chain 1 = CAMUS-calibrated rolling quality light + view): run the release built by deploy_board_v5.py prepare in
place of the service for the acceptance runs, so the binary tested is the one activate installs.

  start   stop the service, run <release>/tcp_image_server (release + sha256 from deployment_candidate_v526.json here: board_check_v5/deployment.json is put back to the active v5.25 manifest after prepare, so nothing activates the candidate before the user says so) with the
          unit's ExecStart arguments (nice -20), wait for port 5060, check the running binary's hash
  stop    kill it, start the service, check it is active and still the v5.25 binary
  status  service state, running binaries, hashes

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe devrun.py start|stop|status
"""
import json
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import b  # noqa: E402

SVC = "tcp-image-server"
LIVE = "/root/tcp_image_rx/tcp_image_server"
V525 = "9851f6fe35636e83ef677db2b6fc405a66ed86572ef5e302c7983d94a29aa939"   # 11-03: the ACTIVE service is v5.26 9851f6fe (chain 2)
MANIFEST = HERE / "deployment_candidate_v526.json"


def sh(c, cmd, timeout=600):
    ch = c.get_transport().open_session(timeout=10)
    ch.set_combine_stderr(True)
    ch.exec_command(cmd)
    out = b""
    end = time.monotonic() + timeout
    while not ch.exit_status_ready() or ch.recv_ready():
        if ch.recv_ready():
            out += ch.recv(65536)
        elif time.monotonic() > end:
            ch.close()
            raise TimeoutError(cmd)
        else:
            time.sleep(0.05)
    return ch.recv_exit_status(), out.decode("utf-8", "replace")


def log(name, text):
    with (HERE / name).open("a", encoding="utf-8") as f:
        f.write(time.strftime("== %Y-%m-%d %H:%M:%S\n") + text + "\n")
    print(text)


def main():
    act = sys.argv[1]
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rel, sha = m["release"], m["new_sha256"]
    c = b.connect()
    try:
        if act == "start":
            rc, out = sh(c, "systemctl show -p ExecStart --value " + SVC)
            argv = shlex.split(out.split("argv[]=", 1)[1].split(";", 1)[0].strip())
            args = " ".join(shlex.quote(a) for a in argv[1:])
            rc, out = sh(c, "systemctl stop %s; sleep 1; cd %s && sha256sum tcp_image_server && "
                            "(setsid nohup nice -n -20 ./tcp_image_server %s > dev_server.log 2>&1 &) ; "
                            "for i in $(seq 1 60); do (echo > /dev/tcp/127.0.0.1/5060) 2>/dev/null && echo PORT_OPEN && break; sleep 0.5; done; "
                            "pgrep -af '[.]/tcp_image_server'; systemctl is-active %s; "
                            "sha256sum /proc/$(pgrep -x tcp_image_serve | head -1)/exe"
                         % (SVC, rel, args, SVC), 120)
            ok = "PORT_OPEN" in out and out.count(sha) >= 2
            log("dev_start.log", "release %s expected %s\nargv %s\n%s\n%s" % (rel, sha, argv, out,
                                                                            "DEV_START_PASS" if ok else "DEV_START_FAIL"))
        elif act == "stop":
            rc, out = sh(c, "pkill -f '[.]/tcp_image_server' ; sleep 1; systemctl start %s; sleep 3; systemctl is-active %s; "
                            "sha256sum %s; tail -n 8 %s/dev_server.log" % (SVC, SVC, LIVE, rel), 120)
            ok = "\nactive" in "\n" + out and V525 in out
            log("dev_stop.log", out + ("\nDEV_STOP_PASS (service back on the active v5.26 9851f6fe)" if ok else "\nDEV_STOP_CHECK"))
        else:
            rc, out = sh(c, "systemctl is-active %s; sha256sum %s; pgrep -af tcp_image_server; uptime" % (SVC, LIVE))
            print(out)
    finally:
        c.close()


if __name__ == "__main__":
    main()
