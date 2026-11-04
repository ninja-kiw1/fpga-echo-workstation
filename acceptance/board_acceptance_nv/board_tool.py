"""Serial SSH/SFTP operations with persistent acceptance evidence."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tcp_image/.board_tools"))
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def connect():
    client = paramiko.SSHClient()
    keys = RESULTS / "known_hosts"
    if keys.exists():
        client.load_host_keys(str(keys))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("192.168.1.254", username="root", password="root",
                   timeout=10, banner_timeout=10, auth_timeout=10,
                   allow_agent=False, look_for_keys=False)
    client.save_host_keys(str(keys))
    client.get_transport().set_keepalive(10)
    return client


def command(client, label, cmd, timeout):
    path = RESULTS / (label + ".log")
    stamp = datetime.now(timezone.utc).isoformat()
    print(f"START {label} {stamp}", flush=True)
    with path.open("w", encoding="utf-8") as log, path.with_suffix(".raw").open("wb") as raw:
        log.write(f"PC_UTC {stamp}\nCOMMAND {cmd}\n")
        channel = client.get_transport().open_session(timeout=10)
        channel.set_combine_stderr(True)
        channel.exec_command(cmd)
        deadline = time.monotonic() + timeout
        heartbeat = time.monotonic()
        while True:
            if channel.recv_ready():
                payload = channel.recv(65536)
                raw.write(payload)
                raw.flush()
                try:
                    data = payload.decode("utf-8")
                except UnicodeDecodeError:
                    data = payload.decode("gb18030", "replace")
                log.write(data)
                log.flush()
                print(data, end="", flush=True)
            elif channel.exit_status_ready():
                break
            else:
                if time.monotonic() > deadline:
                    channel.close()
                    raise TimeoutError(f"{label} exceeded {timeout}s; remote outcome requires inspection")
                if time.monotonic() - heartbeat > 30:
                    print(f"RUNNING {label}", flush=True)
                    heartbeat = time.monotonic()
                time.sleep(0.1)
        rc = channel.recv_exit_status()
        log.write(f"\nSSH_EXITCODE {rc}\n")
        print(f"\nSSH_EXITCODE {rc} {label}", flush=True)
        (RESULTS / (label + ".status.json")).write_text(
            json.dumps(dict(label=label, pc_utc=stamp, exitcode=rc, command=cmd), indent=2),
            encoding="utf-8")
        if rc:
            raise RuntimeError(f"{label} failed with exit code {rc}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    cmd = sub.add_parser("command")
    cmd.add_argument("label")
    cmd.add_argument("command")
    cmd.add_argument("--timeout", type=int, default=120)
    for action in ("upload", "download"):
        p = sub.add_parser(action)
        p.add_argument("source")
        p.add_argument("destination")
    args = parser.parse_args()
    client = connect()
    try:
        if args.action == "command":
            command(client, args.label, args.command, args.timeout)
        else:
            with client.open_sftp() as sftp:
                if args.action == "upload":
                    sftp.put(args.source, args.destination)
                else:
                    destination = Path(args.destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(args.source, str(destination))
            print(f"{args.action.upper()} {args.source} -> {args.destination}", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
