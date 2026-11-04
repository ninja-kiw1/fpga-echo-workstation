"""No-vis acceptance (2026-11-03): f150 (30f06745, deployed 10-17) -> ai_sys_f150nv = f150 without vis_layer, with rollback.

Derived from board_acceptance_f/acceptance.py (same gates, strict tool, inputs and BoardCheck_r1). Differences:
  * new = bitstream/ai_sys_f150nv.bin (ai/bd/build_ai_sys_f_novis.tcl: same p6757 conv / elt / pp cores, clock, addresses;
    vis_layer, its crossbar port and its data-interconnect port removed); backup /lib/firmware/ai_sys.bin.before_nv;
  * the new sha256 goes ONLY into /lib/firmware/ai_sys_pp.sha256; it must NOT be in ai_sys_vis.sha256 (0xA004_0000 has no
    slave any more -- touching it hangs the PS). Gate: the server log says vis is off because the firmware is not listed;
  * server v5.26 (9851f6fe) before and after, unchanged; tools and BoardCheck_r1 are compiled from its release sources;
  * no vis vectors; after the regression: the v5.26 heart live runs (run_board.py, srtp-vision) + host_check.py (skin venv),
    regression = board_acceptance_nv/regression.py (v5.24 groups minus v516);
  * timing: recorded, not gated (bit-exactness is the gate); the PL fundus tile overlay now runs on the PS.

  python acceptance.py baseline        # on f150: backup, tools, strict + profile, BoardCheck baseline, pp vectors, TCP
  python acceptance.py deploy_tests    # install, reboot, BoardCheck, strict, pp vectors, TCP, regression, heart runs
--- original f notes ---
F clock-raise acceptance: r2 (100 MHz) -> ai_sys_<variant> (r2 + PL MMCM, variant f150 = 150 MHz / f125 = 125 MHz), with rollback.

Derived from board_acceptance_r2/acceptance.py (same gates, strict tool, inputs and BoardCheck_r1). Differences:
  * old firmware = r2 (81d0f462..., deployed 10-15), new = bitstream/ai_sys_<variant>.bin (build ai/bd/build_ai_sys_f_clk.tcl,
    four HLS cores re-synthesized at 6.757 ns in ai/build/f_*_p6757, all four cosim PASS); backup /lib/firmware/ai_sys.bin.before_f;
  * pl_clk0 stays 100 MHz (boot.bin / PS untouched); the MMCM inside the bitstream drives all PL logic and HPM0 / HPM1;
  * the PS server is v5.17 (51780377, J1; was v5.16 when this script was first written) before and after (it uses
    vis_layer, so the new sha256 goes into both fingerprint lists before the reboot); BoardCheck_r1 is compiled against
    the v5.17 release sources; v5.17's unet_ai.c has the J1 readback thread, so every tool and BoardCheck_r1 links -lpthread;
  * vis_layer vectors run before AND after (timing comparison), regression = board_acceptance_f/regression.py
    (v5.16 groups + verify_board_v516 + cardiac --board + 5000-frame stability);
  * timing is expected to get FASTER: recorded, not gated (bit-exactness is the gate).
--- original r2 notes ---
r2 bitstream acceptance: xu2dp_e2b_pp_r1a -> xu2dp_e2b_pp_r2 (vis_layer + AXI-Lite crossbar control path), with rollback.

Derived from board_acceptance_r1/acceptance.py (same gates, same strict tool, inputs and BoardCheck_r1). Differences:
  * old firmware = r1a (c3d5645c..., deployed 10-14), new = bitstream/ai_sys_xu2dp_e2b_pp_r2.bin; backup
    /lib/firmware/ai_sys.bin.before_r2; the PS server stays v5.15 for this stage (it does not know vis_layer, so the
    new address is never touched by it); v5.16 is deployed afterwards with deploy_board_v5.py;
  * the new sha256 goes into /lib/firmware/ai_sys_pp.sha256 (pp_layer unchanged at 0xA003_0000) and into the new
    /lib/firmware/ai_sys_vis.sha256 (vis_layer at 0xA004_0000);
  * extra gate: the vis_layer vectors of ai/vis_hls (every BRAM word checked) run directly on the PL (vis_board_test);
  * control path: sc_axil + pc_axil -> pc_axil + ctl_xbar (AXI-Lite crossbar): every register access of BoardCheck,
    the strict tool, pp_board_test and the 17-program regression goes through it.
Original r1 notes (derived from board_acceptance_pp):
  * old firmware = e2b_pp (21aa27b6..., deployed 10-12), new = bitstream/ai_sys_xu2dp_e2b_pp_<variant>.bin
    (variant r1 = 1a line-buffer preprocessing + 1b shallow m_axi buffers; r1a = 1a only);
    backup /lib/firmware/ai_sys.bin.before_r1 (never overwritten if it exists);
  * the new firmware has the same pp_layer at 0xA003_0000: its sha256 is appended to /lib/firmware/ai_sys_pp.sha256;
  * img_proc_axil VERSION becomes 0x494D5004 (line buffer; modes 0 / 4 pushed twice). The PS server must be v5.15+
    (src/axilproc.c accepts 3 and 4); v5.14 exits on VERSION 4. BoardCheck: a separate build /root/Fz3b_BoardCheck_r1
    (bc_hw.c accepts both versions) compiled against the v5.15 release sources; the original BoardCheck stays as is;
  * BoardCheck gate: 31 items, no FAIL, all mismatch 0; WARN (>20% slower than the old-firmware baseline) is allowed
    only on modes 0 / 4 (two passes, expected) and is recorded;
  * timing: strict tool + per-node profile + TCP before / after, the AI (ap / whole net) must not get slower beyond noise.

  python acceptance.py baseline [--variant r1]       # on e2b_pp: backup, tools, strict + profile, BoardCheck baseline, pp vectors, TCP
  python acceptance.py deploy_tests [--variant r1]   # install, reboot, BoardCheck, strict, pp vectors, TCP, regression
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from board_tool import ROOT, RESULTS, command, connect

RX = ROOT / 'tcp_image/tcp_image_rx'
REMOTE = '/data/nv_acceptance'   # root fs had only 282 MB free on 11-03
TOOLS = REMOTE + '/tools'
OLD_HASH = '30f06745619038236765e237bedec98229f93df7c3824ef48eb48b98807c5685'   # f150
BACKUP = '/lib/firmware/ai_sys.bin.before_nv'
PP_LIST = '/lib/firmware/ai_sys_pp.sha256'
VIS_LIST = '/lib/firmware/ai_sys_vis.sha256'
VEC = RX / 'ai/pp_hls/vectors'
BC_LOCAL = ROOT / 'board_acceptance_r1/Fz3b_BoardCheck_r1'
BC_REMOTE = '/root/Fz3b_BoardCheck_r1'
SERVER_MIN = '9851f6fe35636e83ef677db2b6fc405a66ed86572ef5e302c7983d94a29aa939'   # v5.26 (11-03; uses vis only if listed)
SERVER_SRC = '/root/tcp_image_rx/releases/v5_20261103T033243Z'                    # its sources (for BoardCheck_r1)
GUI = 'pids=$(pgrep -f "^python3 /root/Fz3b_BoardCheck/gui/board_check_gui.py$" || true); if [ -n "$pids" ]; then kill -{sig} $pids; fi'
VARIANT = 'f150nv'
SRC_FILES = ('unet_ai.c', 'unet_ai.h', 'unet_net.h', 'unet_net_data.c', 'unet_models.c', 'unet_models.h',
             'pp_ai.c', 'pp_ai.h', 'ai_view.c', 'ai_view.h', 'image_proto.h')
SKIN_PY = ROOT / 'skin/.venv/Scripts/python.exe'
UI_PY = Path('C:/Users/21888/anaconda3/envs/srtp-vision/python.exe')


def new_paths():
    new = RX / f'bitstream/ai_sys_{VARIANT}.bin'
    sha = Path(str(new) + '.sha256')
    return new, (sha.read_text().split()[0] if sha.exists() else None)


def state_path():
    return RESULTS / f'state_{VARIANT}.json'


def save(state):
    state['updated_pc_utc'] = datetime.now(timezone.utc).isoformat()
    state_path().write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')


def read(client, cmd):
    _, out, err = client.exec_command(cmd, timeout=30)
    value = out.read().decode('utf-8', 'replace')
    error = err.read().decode('utf-8', 'replace')
    if out.channel.recv_exit_status(): raise RuntimeError(value + error)
    return value.strip()


def run(label, cmd, timeout=120):
    client = connect()
    try: command(client, label, cmd, timeout)
    finally: client.close()
    return (RESULTS / (label + '.log')).read_text(encoding='utf-8')


def pause_gui(client):
    read(client, GUI.format(sig='STOP'))


def inputs():
    """the e2b / pp acceptance inputs, byte for byte"""
    src = ROOT / 'board_acceptance_r1/inputs'
    directory = RESULTS / 'inputs'
    if directory.exists(): shutil.rmtree(directory)
    shutil.copytree(src, directory)
    prev = {r['path']: r['sha256'] for r in json.loads((ROOT / 'board_acceptance_r1/input_manifest.json').read_text(encoding='utf-8'))}
    records = []
    for p in sorted(directory.iterdir()):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if prev.get(p.name) not in (None, h): raise RuntimeError('Input differs from pp acceptance: ' + p.name)
        records.append(dict(path=p.name, bytes=p.stat().st_size, sha256=h))
    (RESULTS / 'input_manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    return directory


def prepare(state):
    """Backup the running f150 firmware (never overwrite an existing backup), build strict / pp tools and BoardCheck_r1."""
    directory = inputs()
    text = run('baseline_prepare', 'set -eu; systemctl stop tcp-image-server; ' + GUI.format(sig='STOP') +
               f'; mkdir -p {REMOTE}/before/boardcheck {TOOLS} {TOOLS}/vectors; '
               f'echo "{OLD_HASH}  /lib/firmware/ai_sys.bin" | sha256sum -c -; '
               f'if [ -e {BACKUP} ]; then echo "{OLD_HASH}  {BACKUP}" | sha256sum -c -; else cp -a /lib/firmware/ai_sys.bin {BACKUP}; fi; '
               f'sha256sum {BACKUP} /root/tcp_image_rx/tcp_image_server; test -d {SERVER_SRC}/src; df -h / | tail -1')
    if OLD_HASH not in text: raise RuntimeError('Backup hash wrong')
    server = re.search(r'([0-9a-f]{64})\s+/root/tcp_image_rx/tcp_image_server', text).group(1)
    if server != SERVER_MIN: raise RuntimeError('PS server is not v5.26 (%s): update SERVER_MIN / SERVER_SRC first' % server)
    state['old_firmware_sha256'] = OLD_HASH
    state['old_server_sha256'] = server
    client = connect()
    try:
        with client.open_sftp() as sf:
            sf.put(str(RX / 'test/pp_board_test.c'), TOOLS + '/pp_board_test.c')
            sf.put(str(RESULTS / 'strict_net_test.c'), TOOLS + '/strict_net_test.c')
            for p in directory.iterdir(): sf.put(str(p), TOOLS + '/' + p.name)
            for p in VEC.iterdir():
                if p.suffix in ('.bin', '.txt'): sf.put(str(p), TOOLS + '/vectors/' + p.name)
            try: sf.mkdir(BC_REMOTE)
            except IOError: pass
            for sub in ('src',):
                try: sf.mkdir(BC_REMOTE + '/' + sub)
                except IOError: pass
                for p in (BC_LOCAL / sub).iterdir():
                    if p.is_file(): sf.put(str(p), f'{BC_REMOTE}/{sub}/{p.name}')
            sf.put(str(BC_LOCAL / 'makefile'), BC_REMOTE + '/makefile')
    finally: client.close()
    run('test_tool_build', f'set -eu; cd {TOOLS}; for f in {" ".join(SRC_FILES)}; do cp {SERVER_SRC}/src/$f .; done; gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. unet_ai.c unet_net_data.c unet_models.c strict_net_test.c -o strict_net_test -lpthread; '
        f'gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. pp_board_test.c pp_ai.c unet_ai.c unet_net_data.c unet_models.c ai_view.c -o pp_board_test -lm -lpthread; '
        f'sha256sum strict_net_test pp_board_test unet_ai.c pp_ai.c; ls vectors | wc -l; df -h / | tail -1; '
        f'cd {BC_REMOTE}; rm -rf build_board; make CC=gcc RX={SERVER_SRC} LDLIBS="-lm -lpthread" '
        f'RXSRC="{SERVER_SRC}/src/axilproc.c {SERVER_SRC}/src/ai_view.c {SERVER_SRC}/src/unet_ai.c {SERVER_SRC}/src/unet_net_data.c {SERVER_SRC}/src/unet_models.c"; '
        f'sha256sum build_board/board_check; grep -c 494D5004 {SERVER_SRC}/src/axilproc.h', 400)
    save(state)


def parse_profile(text):
    """Per-node ap_us, mean over the steady-state runs (the first block is a cold run)."""
    blocks = re.findall(r"\[prof\] per-node:.*?\n((?:\[prof\]\s+\d+\s+\d+\s+\d+\n)+)", text)
    runs = [{int(m.group(1)): int(m.group(2)) for m in re.finditer(r"\[prof\]\s+(\d+)\s+(\d+)\s+\d+", b)} for b in blocks]
    steady = runs[1:] or runs
    return {i: round(sum(r[i] for r in steady) / len(steady)) for i in steady[0]}


def direct(tag):
    run(tag + '_exclusive', 'set -eu; systemctl stop tcp-image-server; test -z "$(pgrep -x tcp_image_serve || true)"; ' + GUI.format(sig='STOP'))
    samples = []
    for name, model, res, count in (('fundus32', 0, 32, 1), ('fundus512', 0, 512, 10), ('skin128', 1, 128, 10), ('echo128', 2, 128, 10)):
        text = run(f'{tag}_{name}', f'set -eu; cd {TOOLS}; timeout 180 ./strict_net_test {name}.rgb {name}.i8 {model} {res} {count}', 220)
        if 'STRICT_NET_PASS' not in text or re.search(r'mismatch=[1-9]', text): raise RuntimeError('Strict hardware compare failed: ' + name)
        samples.extend(json.loads(line[7:]) for line in text.splitlines() if line.startswith('SAMPLE '))
    (RESULTS / (tag + '_performance.json')).write_text(json.dumps(samples, indent=2), encoding='utf-8')
    profiles = {}
    for name, model, res in (('fundus512', 0, 512), ('skin128', 1, 128)):
        text = run(f'{tag}_profile_{name}', f'set -eu; cd {TOOLS}; UNET_PROFILE=1 timeout 180 ./strict_net_test {name}.rgb {name}.i8 {model} {res} 3', 220)
        if 'STRICT_NET_PASS' not in text: raise RuntimeError('Profile run failed: ' + name)
        profiles[name] = parse_profile(text)
    (RESULTS / (tag + '_node_profile.json')).write_text(json.dumps(profiles, indent=2), encoding='utf-8')


def boardcheck(tag, extra, allow_warn=()):
    run(tag + '_boardcheck_prepare', 'set -eu; systemctl stop tcp-image-server; ' + GUI.format(sig='STOP') + f'; mkdir -p {REMOTE}/{tag}/boardcheck')
    text = run(tag + '_boardcheck', f'set -eu; cd /root/Fz3b_BoardCheck; timeout 240 {BC_REMOTE}/build_board/board_check -i /root/test.ppm --auto-thr --save --ai --ai-res 512 --ai-golden-x golden/v512_x.bin --ai-golden-final golden/v512_final.bin --outdir {REMOTE}/{tag}/boardcheck {extra}', 280)
    client = connect()
    try:
        with client.open_sftp() as sf:
            sf.get(f'{REMOTE}/{tag}/boardcheck/report_latest.md', str(RESULTS / (tag + '_boardcheck_report.md')))
    finally: client.close()
    rep = (RESULTS / (tag + '_boardcheck_report.md')).read_text(encoding='utf-8')
    rows = re.findall(r'^\| (PASS|FAIL|WARN|SKIP) \| ([^|]+?) \|', rep, re.M)
    warns = [n for s, n in rows if s == 'WARN']
    bad = [n for s, n in rows if s in ('FAIL', 'SKIP')] + [n for n in warns if not any(a in n for a in allow_warn)]
    if len(rows) != 31 or bad or '[FAIL]' in text or 'mismatch 0/262144' not in text:
        raise RuntimeError('BoardCheck mandatory gate failed: %s rows=%d bad=%s' % (tag, len(rows), bad))
    return dict(items=len(rows), warn=warns)


def pp_vectors(tag):
    text = run(tag + '_pp_vectors', f'set -eu; systemctl stop tcp-image-server; test -z "$(pgrep -x tcp_image_serve || true)"; '
               f'cd {TOOLS}; timeout 600 ./pp_board_test vectors cases.txt 3', 660)
    if 'PP_BOARD_PASS' not in text or 'failures' not in text: raise RuntimeError('pp vectors failed on the board')
    rows = [l for l in text.splitlines() if l.startswith('TIME ')]
    (RESULTS / (tag + '_pp_timing.txt')).write_text('\n'.join(rows) + '\n', encoding='utf-8')


def tcp(tag):
    run(tag + '_tcp_start', 'set -eu; systemctl start tcp-image-server; systemctl is-active tcp-image-server; ' + GUI.format(sig='CONT'))
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    with (RESULTS / f'{tag}_tcp_perf.log').open('w', encoding='utf-8') as log:
        rc = subprocess.run([sys.executable, '-u', str(RESULTS / 'tcp_perf.py'), tag], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT).returncode
    if rc or 'TCP_PERF_PASS' not in (RESULTS / f'{tag}_tcp_perf.log').read_text(encoding='utf-8'):
        raise RuntimeError('TCP performance run failed: ' + tag)


def baseline():
    new, new_hash = new_paths()
    if state_path().exists(): raise RuntimeError('Baseline state exists; use the saved run rather than overwrite')
    if new_hash is None: raise RuntimeError(f'Build the {VARIANT} bitstream (and its .sha256) first')
    state = dict(phase='baseline', variant=VARIANT, new_firmware_sha256=new_hash, tests={})
    prepare(state)
    direct('before')
    state['boardcheck_before'] = boardcheck('before', '--save-baseline')
    pp_vectors('before')
    state['baseline_passed'] = True
    state['phase'] = 'baseline_complete'
    save(state)
    tcp('before')


def wait_reboot(old_boot):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            client = connect()
            boot = read(client, 'cat /proc/sys/kernel/random/boot_id')
            client.close()
            if boot != old_boot:
                print('REBOOT_CONFIRMED ' + boot, flush=True)
                return
        except Exception as exc:
            print('WAITING_FOR_BOARD ' + type(exc).__name__, flush=True)
        time.sleep(5)
    raise RuntimeError('Board did not return with a new boot ID')


def reboot_to_new(state):
    new, new_hash = new_paths()
    if new_hash is None or hashlib.sha256(new.read_bytes()).hexdigest() != new_hash: raise RuntimeError('New firmware hash mismatch')
    client = connect()
    try:
        pause_gui(client)
        boot = read(client, 'cat /proc/sys/kernel/random/boot_id')
        if read(client, 'sha256sum /lib/firmware/ai_sys.bin').split()[0] != OLD_HASH: raise RuntimeError('Current firmware changed since baseline')
        with client.open_sftp() as sf: sf.put(str(new), f'{REMOTE}/{new.name}')
        command(client, 'deploy_new', f'set -eu; systemctl stop tcp-image-server; test -z "$(pgrep -x tcp_image_serve || true)"; '
                f'echo "{new_hash}  {REMOTE}/{new.name}" | sha256sum -c -; echo "{OLD_HASH}  {BACKUP}" | sha256sum -c -; '
                f'cp {REMOTE}/{new.name} /lib/firmware/ai_sys.bin; '
                f'grep -q {new_hash} {PP_LIST} 2>/dev/null || echo "{new_hash}  ai_sys_{VARIANT} (f150 without vis_layer; pp_layer @0xA0030000, line-buffer img_proc 0x494D5004)" >> {PP_LIST}; '
                f'if grep -q {new_hash} {VIS_LIST}; then echo "new firmware must not be in {VIS_LIST}"; exit 1; fi; '
                f'sync; sha256sum /lib/firmware/ai_sys.bin; cat {PP_LIST} {VIS_LIST}', 120)
        state['new_deployed'] = True
        state['phase'] = 'new_boot'
        save(state)
        client.exec_command('reboot')[1].read()
    finally: client.close()
    wait_reboot(boot)
    time.sleep(8)
    text = run('new_boot_state', 'set -eu; sha256sum /lib/firmware/ai_sys.bin /root/tcp_image_rx/tcp_image_server; cat /sys/class/fpga_manager/fpga0/state; '
               'systemctl is-active tcp-image-server plbram plbdma; journalctl -u ai-sys-bitstream -b --no-pager; journalctl -u tcp-image-server -b --no-pager | grep -E "axil|vis|pp\\]" ; '
               'grep -E "pl0_ref " /sys/kernel/debug/clk/clk_summary; ss -ltnp')
    if new_hash not in text or state['old_server_sha256'] not in text or 'operating' not in text or '0x494D5004' not in text \
            or 'pp_layer ready' not in text or 'vis_layer ready' in text or ':5060' not in text \
            or 'registration / overlay off: firmware not listed' not in text:
        raise RuntimeError('New boot checks failed')


def regressions(state):
    run('tcp_tests_start', 'set -eu; systemctl start tcp-image-server; systemctl is-active tcp-image-server')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    with (RESULTS / 'regression_run.log').open('w', encoding='utf-8') as log:
        rc = subprocess.run([sys.executable, '-u', str(RESULTS / 'regression.py')], cwd=RESULTS, env=env, stdout=log,
                            stderr=subprocess.STDOUT).returncode
    text = (RESULTS / 'regression_run.log').read_text(encoding='utf-8')
    if rc or 'REGRESSION_NV_PASS' not in text: raise RuntimeError('regression failed')
    state['tests']['regression'] = 'REGRESSION_NV_PASS'
    save(state)


def heart(state):
    """the v5.26 heart live runs (keyframe + live chains, CAMUS chain 2) and their host integer references"""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    for label, py, script in (('heart_runs', UI_PY, 'run_board.py'), ('heart_host_check', SKIN_PY, 'host_check.py')):
        with (RESULTS / f'{label}.log').open('w', encoding='utf-8') as log:
            rc = subprocess.run([str(py), '-I', '-u', str(RESULTS / script)], cwd=RESULTS, env=env, stdout=log,
                                stderr=subprocess.STDOUT).returncode
        if rc: raise RuntimeError(label + ' failed')
    if 'HOST_CHECK_PASS' not in (RESULTS / 'heart_host_check.log').read_text(encoding='utf-8'):
        raise RuntimeError('heart host check failed')
    state['tests']['heart'] = 'HOST_CHECK_PASS'
    save(state)


def rollback(state):
    print('ROLLBACK_START', flush=True)
    client = connect()
    try:
        pause_gui(client)
        boot = read(client, 'cat /proc/sys/kernel/random/boot_id')
        command(client, 'rollback', f'set -eu; systemctl stop tcp-image-server; echo "{OLD_HASH}  {BACKUP}" | sha256sum -c -; cp -a {BACKUP} /lib/firmware/ai_sys.bin; sync', 120)
        client.exec_command('reboot')[1].read()
    finally: client.close()
    wait_reboot(boot)
    time.sleep(8)
    text = run('rollback_state', 'sha256sum /lib/firmware/ai_sys.bin /root/tcp_image_rx/tcp_image_server; cat /sys/class/fpga_manager/fpga0/state; systemctl is-active tcp-image-server plbram plbdma; journalctl -u ai-sys-bitstream -b --no-pager')
    state['rollback_verified'] = OLD_HASH in text and state['old_server_sha256'] in text and 'operating' in text
    save(state)
    if state['rollback_verified']:
        run('rollback_minimal_prepare', 'set -eu; systemctl stop tcp-image-server')
        text = run('rollback_minimal', f'set -eu; cd {TOOLS}; timeout 60 ./strict_net_test fundus32.rgb fundus32.i8 0 32 1', 90)
        state['rollback_minimal_passed'] = 'STRICT_NET_PASS' in text and 'mismatch=0/1024' in text
        run('rollback_service_restore', 'set -eu; systemctl start tcp-image-server; systemctl is-active tcp-image-server plbram plbdma; ' + GUI.format(sig='CONT'))
        save(state)


def main():
    global VARIANT
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['baseline', 'deploy_tests'])
    parser.add_argument('--variant', default='f150nv', choices=['f150nv'])
    args = parser.parse_args()
    VARIANT = args.variant
    if args.stage == 'baseline': baseline(); return
    state = json.loads(state_path().read_text(encoding='utf-8'))
    if not state.get('baseline_passed') or state.get('new_deployed'): raise RuntimeError('Unexpected acceptance state')
    try:
        reboot_to_new(state)
        run('after_boardcheck_baseline', f'set -eu; mkdir -p {REMOTE}/after/boardcheck; cp {REMOTE}/before/boardcheck/baseline.txt {REMOTE}/after/boardcheck/baseline.txt')
        state['boardcheck_after'] = boardcheck('after', '--compare-baseline', allow_warn=('mode0', 'mode4'))
        save(state)
        direct('after')
        state['direct_passed'] = True
        save(state)
        pp_vectors('after')
        state['pp_vectors_passed'] = True
        save(state)
        tcp('after')
        regressions(state)
        heart(state)
        run('final_state', 'set -eu; sha256sum /lib/firmware/ai_sys.bin /root/tcp_image_rx/tcp_image_server; cat /sys/class/fpga_manager/fpga0/state; systemctl is-active tcp-image-server plbram plbdma; journalctl -u tcp-image-server -b --no-pager | tail -60; ' + GUI.format(sig='CONT'))
        state['phase'] = 'complete'
        state['board_regression_passed'] = True
        save(state)
        print('NV_ACCEPTANCE_PASS ' + VARIANT, flush=True)
    except BaseException as exc:
        state['failed_phase'] = state['phase']
        state['failure'] = repr(exc)
        state['board_regression_passed'] = False
        save(state)
        if state.get('new_deployed'):
            try: rollback(state)
            except BaseException as rb:
                state['rollback_error'] = repr(rb); save(state)
        raise


if __name__ == '__main__': main()
