"""Install / switch / remove the ISIC skin server on the FZ3B (plan A: two programs).

Never reloads the FPGA and never touches the fundus program or its unit:
the fundus executable /root/tcp_image_rx/tcp_image_server, its unit file and
/lib/firmware/ai_sys.bin are hashed before and after every action and must
not change.

    python deploy_skin.py status
    python deploy_skin.py install          # build + install unit, does NOT switch
    python deploy_skin.py switch skin      # stop fundus server, start skin server
    python deploy_skin.py switch fundus    # back to the boot default
    python deploy_skin.py uninstall        # switch to fundus, remove skin unit and binary

Run with E:\\PLD\\skin\\.venv\\Scripts\\python.exe (paramiko comes from
tcp_image/.board_tools, same as deploy_board_v4.py). Logs: board/board_logs/.
"""
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TCP = HERE.parents[1] / 'tcp_image'
sys.path.insert(0, str(TCP / '.board_tools'))
import paramiko  # noqa: E402

HOST = '192.168.1.254'
STAGE = HERE / 'stage'
LOGS = HERE / 'board_logs'
MANIFEST = LOGS / 'skin_deployment.json'
FUNDUS_BIN = '/root/tcp_image_rx/tcp_image_server'
FUNDUS_UNIT = '/etc/systemd/system/tcp-image-server.service'
SKIN_UNIT = '/etc/systemd/system/tcp-image-server-skin.service'
SKIN_BIN = '/root/skin/tcp_image_server_skin'
FIRMWARE = '/lib/firmware/ai_sys.bin'


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    LOGS.mkdir(exist_ok=True)
    c = paramiko.SSHClient()
    known = TCP / 'tcp_image_ui' / 'board_check_v4' / 'known_hosts'   # same host key store as v4 deploy
    if known.exists():
        c.load_host_keys(str(known))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username='root', password='root', timeout=10, allow_agent=False, look_for_keys=False)

    def run(command, timeout=60, check=True):
        print('$', command, flush=True)
        _, out, _ = c.exec_command(command + ' 2>&1', timeout=timeout)
        value = out.read().decode('utf-8', 'replace')
        status = out.channel.recv_exit_status()
        print(value, end='', flush=True)
        with (LOGS / f'deploy_{action}.log').open('a', encoding='utf-8') as f:
            f.write(f'$ {command}\n{value}\nexit={status}\n')
        if check and status:
            raise RuntimeError(f'remote exit {status}: {command}')
        return value

    def protected():
        """Hashes of everything that belongs to the fundus system."""
        return run(f'sha256sum {FUNDUS_BIN} {FUNDUS_UNIT} {FIRMWARE}').split()[0::2]

    def services():
        return run('systemctl is-active tcp-image-server tcp-image-server-skin '
                   'ai-sys-bitstream plbram plbdma', check=False).split()

    try:
        before = protected()
        if action == 'status':
            services()
            run(f'ls -l {SKIN_BIN} {SKIN_UNIT}; ss -ltnp | grep 5060', check=False)
        elif action == 'install':
            tag = 'skin_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            remote = f'/root/skin_build/{tag}'
            files = sorted((STAGE / 'src').glob('*.[ch]')) + [STAGE / 'makefile', HERE / 'install_skin.sh',
                                                                HERE / 'skin_mode.sh']
            run(f'mkdir -p {remote}/src')
            sources = {}
            with c.open_sftp() as s:
                for p in files:
                    rel = p.relative_to(STAGE).as_posix() if p.is_relative_to(STAGE) else p.name
                    s.put(str(p), f'{remote}/{rel}')
                    sources[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            run(f'cd {remote} && sed -i "s/\\r$//" install_skin.sh skin_mode.sh && sh install_skin.sh 128', 300)
            manifest = dict(tag=tag, remote=remote, sources=sources, utc=datetime.now(timezone.utc).isoformat(),
                            skin_sha256=run(f'sha256sum {SKIN_BIN}').split()[0],
                            skin_unit=run(f'cat {SKIN_UNIT}'), protected_sha256=before)
            MANIFEST.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
            services()
            print('SKIN_INSTALL_PASS (not switched; fundus still serving)', flush=True)
        elif action == 'switch':
            target = sys.argv[2]
            assert target in ('skin', 'fundus'), target
            unit = 'tcp-image-server-skin' if target == 'skin' else 'tcp-image-server'
            other = 'tcp-image-server' if target == 'skin' else 'tcp-image-server-skin'
            run(f'systemctl start {unit}.service')
            run('sleep 2')
            act = dict(zip(('tcp-image-server', 'tcp-image-server-skin'), services()[:2]))
            if act[unit] != 'active' or act[other] == 'active':
                raise RuntimeError(f'unexpected service states {act}')
            run('ss -ltnp | grep 5060')
            print(f'SWITCH_{target.upper()}_PASS', flush=True)
        elif action == 'uninstall':
            run('systemctl start tcp-image-server.service && sleep 2 && systemctl is-active tcp-image-server')
            run(f'systemctl stop tcp-image-server-skin.service; rm -f {SKIN_UNIT} && systemctl daemon-reload && '
                f'rm -rf /root/skin', check=False)
            services()
            print('SKIN_UNINSTALL_PASS', flush=True)
        else:
            raise ValueError('unknown action ' + action)
        after = protected()
        if after != before:
            raise RuntimeError(f'fundus program / unit / firmware changed! {before} -> {after}')
        print('fundus program, unit and firmware unchanged:', ' '.join(h[:12] for h in after), flush=True)
    finally:
        c.close()


if __name__ == '__main__':
    main()
