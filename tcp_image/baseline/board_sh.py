"""Run one shell command on the FZ3B over SSH (same access as deploy_board_v4.py) and print the output.

    python board_sh.py "<command>"
"""
import sys
from pathlib import Path

TCP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TCP / '.board_tools'))
import paramiko  # noqa: E402


def run(command, timeout=600):
    c = paramiko.SSHClient()
    known = TCP / 'tcp_image_ui' / 'board_check_v4' / 'known_hosts'
    if known.exists():
        c.load_host_keys(str(known))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('192.168.1.254', username='root', password='root', timeout=10, allow_agent=False, look_for_keys=False)
    try:
        _, out, _ = c.exec_command(command + ' 2>&1', timeout=timeout)
        text = out.read().decode('utf-8', 'replace')
        return text, out.channel.recv_exit_status()
    finally:
        c.close()


if __name__ == '__main__':
    text, status = run(sys.argv[1])
    sys.stdout.write(text)
    sys.exit(status)
