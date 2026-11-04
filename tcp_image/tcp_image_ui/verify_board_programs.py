# -*- coding: utf-8 -*-
"""Board acceptance for program packages (server v5.9+, src/progpkg.c): models installed at run time.

A  repackage all 17 built-in models as packages (ids 32..48) with make_program_pkg.py and install them
   (boot/install_program.py): every package must pass its on-board self-test (PL logits == HLS reference);
B  for every model, 3 test inputs at the model's default size: package logits == built-in logits, and for the
   first input == the HLS bit-exact NumPy reference (so packages, built-ins and the reference agree);
C  multi-program pipeline with packages (op_median3 -> echo_lv) == the same pipeline with built-ins;
D  board-side tracking with packages (echo_lv reset, echo_seq steps) == the same with built-ins;
E  an id that is neither built in nor loaded closes the connection; the server keeps serving;
F  a package whose expected logits were altered by one value is rejected by the on-board self-test,
   and one with a corrupted body by the CRC check; neither id becomes usable;
G  hot reload: remove one package -> its id is gone, install it again -> back, without restarting;
H  AI time per model, built-in vs package.
All test packages are removed at the end (the board is left with an empty program directory).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_programs.py
"""
import json
import struct
import sys
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RX = HERE.parent / 'tcp_image_rx'
sys.path.insert(0, str(RX / 'ai' / 'net'))
sys.path.insert(0, str(RX / 'boot'))
sys.path.insert(0, str(HERE))

import numpy as np                  # noqa: E402

import gen_unet_models as gum       # noqa: E402
import imagelink                    # noqa: E402
import install_program as inst      # noqa: E402
import make_program_pkg as mpk      # noqa: E402
import proto                        # noqa: E402

OUT = HERE / 'board_check_v5' / 'programs_acceptance.json'
BASE = 32
F = proto.FLAG_STATS | proto.FLAG_C_LOGIT
NAMES = [m[0] for m in gum.MODELS]


def inputs(tag, side, n=3):
    return [mpk.selftest_input('%s_in%d' % (tag, i), side) for i in range(n)]


def trip(link, rgb, model, thr, **kw):
    w = rgb.shape[0]
    st, lg, _, ms = link.v6c_round_trip(np.ascontiguousarray(rgb).tobytes(), w, kind=proto.V6C_KIND_RGB,
                                        model0=model, thr=thr, flags=F, **kw)
    if kw.get('extra'):
        return st, [np.frombuffer(x, np.int8).reshape(w, w) for x in lg], ms
    return st, np.frombuffer(lg, np.int8).reshape(w, w), ms


def loaded(text):
    return {k: v for k, v in inst.table(text).items()}


def main():
    report = dict(utc=datetime.now(timezone.utc).isoformat(), checks={})
    ok = True

    def check(name, cond, **info):
        nonlocal ok
        report['checks'][name] = dict(passed=bool(cond), **info)
        ok &= bool(cond)
        print('%s  %s %s' % ('PASS' if cond else 'FAIL', name, json.dumps(info, ensure_ascii=False)[:300]),
              flush=True)

    tmp = Path(tempfile.mkdtemp(prefix='upk_accept_'))
    files = []
    for k, (tag, vec, shift, thr, roi) in enumerate(gum.MODELS):
        data, _ = mpk.build(vec or gum.FUNDUS_VEC, tag + '_pkg', BASE + k, shift, thr, roi, max(shift, 2),
                            {'repackaged_builtin': tag})
        p = tmp / ('%02d_%s.upk' % (k, tag))
        p.write_bytes(data)
        files.append(p)
    c = inst.connect()
    try:
        inst.run(c, 'mkdir -p %s && rm -f %s/*.upk' % (inst.DIR, inst.DIR))
        report['server_sha256'] = inst.run(c, 'sha256sum /root/tcp_image_rx/tcp_image_server').split()[0]
        report['firmware_sha256'] = inst.run(c, 'sha256sum /lib/firmware/ai_sys.bin').split()[0]
        with c.open_sftp() as s:
            for p in files:
                s.put(str(p), inst.DIR + '/' + p.name)
        t0 = time.time()
        text = inst.reload(c)
        rows = loaded(text)
        report['loaded_txt'] = text
        check('A_install_all_17', len(rows) == 17 and all(r[4].startswith('OK') for r in rows.values()),
              reload_s=round(time.time() - t0, 2), results={k: v[4] for k, v in rows.items()})

        link = imagelink.ImageLink('192.168.1.254', 5060, timeout=60)
        timing = {}
        for k, (tag, vec, shift, thr, roi) in enumerate(gum.MODELS):
            side = 512 >> shift
            same, ref_eq, ai_b, ai_p = True, None, [], []
            for i, rgb in enumerate(inputs(tag, side)):
                sb, lb, _ = trip(link, rgb, k, thr)
                sp, lp, _ = trip(link, rgb, BASE + k, thr)
                same &= bool(np.array_equal(lb, lp)) and sb['model'] == k and sp['model'] == BASE + k
                ai_b.append(sb['ai_us'] / 1000.0)
                ai_p.append(sp['ai_us'] / 1000.0)
                if i == 0:
                    ref_eq = bool(np.array_equal(lp, mpk.reference_logits(vec or gum.FUNDUS_VEC, rgb)))
            timing[tag] = dict(res=side, builtin_ms=round(float(np.mean(ai_b)), 2),
                               package_ms=round(float(np.mean(ai_p)), 2))
            check('B_%02d_%s' % (k, tag), same and ref_eq, res=side, package_eq_builtin=same,
                  package_eq_reference=ref_eq)
        report['H_ai_ms'] = timing

        # C pipeline: op_median3 -> (output as grey) -> echo_lv
        rgb = inputs('pipe', 128, 1)[0]
        med, lv = NAMES.index('op_median3'), NAMES.index('echo_lv')
        _, ob, _ = trip(link, rgb, med, 0, extra=[(lv, 1, 0, 1)])
        _, op, _ = trip(link, rgb, BASE + med, 0, extra=[(BASE + lv, 1, 0, 1)])
        check('C_pipeline_packages', all(np.array_equal(a, b) for a, b in zip(ob, op)))

        # D tracking: reset with echo_lv, then 4 steps with echo_seq (state kept on the board)
        seq = NAMES.index('echo_seq')
        frames = inputs('track', 128, 5)

        def run_track(m0, m1):
            outs = []
            for t, f in enumerate(frames):
                g = np.ascontiguousarray(f[:, :, 1])
                st, lg, _, _ = link.v6c_round_trip(g.tobytes(), 128, kind=proto.V6C_KIND_GRAY, model0=m0,
                                                   model1=m1, thr=1, flags=F,
                                                   track=proto.V6C_TRACK_RESET if t == 0 else proto.V6C_TRACK_STEP)
                outs.append((st['model'], np.frombuffer(lg, np.int8)))
            return outs
        tb, tp = run_track(lv, seq), run_track(BASE + lv, BASE + seq)
        check('D_tracking_packages', all(np.array_equal(a[1], b[1]) for a, b in zip(tb, tp)) and
              [m for m, _ in tp] == [BASE + lv] + [BASE + seq] * 4, models=[m for m, _ in tp])

        # E unknown id
        try:
            trip(link, rgb, 200, 1)
            closed = False
        except imagelink.LinkError:
            closed = True
        link.close()
        _, again, _ = trip(link, rgb, BASE + med, 0)
        check('E_unknown_id_closes_server_survives', closed and again.shape == (128, 128))

        # F rejected packages (on the real PL self-test / CRC)
        good = files[1].read_bytes()                         # skin package
        bad = bytearray(good)
        bad[8] = 60
        bad[32:64] = b'tampered'.ljust(32, b'\0')
        bad[-100] = (bad[-100] + 1) & 0xFF                  # one expected logit (int8 wraps)
        struct.pack_into('<I', bad, 28, zlib.crc32(bytes(bad[64:])) & 0xFFFFFFFF)
        crcbad = bytearray(good)
        crcbad[8] = 61
        crcbad[32:64] = b'crcbad'.ljust(32, b'\0')
        crcbad[3000] ^= 0x55
        (tmp / 'zz_tampered.upk').write_bytes(bad)
        (tmp / 'zz_crcbad.upk').write_bytes(crcbad)
        with c.open_sftp() as s:
            s.put(str(tmp / 'zz_tampered.upk'), inst.DIR + '/zz_tampered.upk')
            s.put(str(tmp / 'zz_crcbad.upk'), inst.DIR + '/zz_crcbad.upk')
        link.close()                    # an idle connection would hold the reload until the 10 s timeout
        rows = loaded(inst.reload(c))
        usable = []
        for pid in (60, 61):
            try:
                trip(link, rgb, pid, 1)
                usable.append(pid)
            except imagelink.LinkError:
                link.close()
        check('F_rejects', 'differ' in rows['zz_tampered.upk'][4] and rows['zz_tampered.upk'][4].startswith('REJ')
              and 'CRC' in rows['zz_crcbad.upk'][4] and not usable and
              sum(r[4].startswith('OK') for r in rows.values()) == 17,
              tampered=rows['zz_tampered.upk'][4], crcbad=rows['zz_crcbad.upk'][4], usable=usable)

        # G hot reload: remove the skin package, then install it again
        inst.run(c, 'rm -f %s/zz_*.upk %s/%s' % (inst.DIR, inst.DIR, files[1].name))
        link.close()
        rows = loaded(inst.reload(c))
        try:
            trip(link, rgb, BASE + 1, 1)
            gone = False
        except imagelink.LinkError:
            gone = True
            link.close()
        with c.open_sftp() as s:
            s.put(str(files[1]), inst.DIR + '/' + files[1].name)
        link.close()
        rows2 = loaded(inst.reload(c))
        st, _, _ = trip(link, inputs('skin', 128, 1)[0], BASE + 1, 1)
        check('G_hot_reload', gone and len(rows) == 16 and len(rows2) == 17 and st['model'] == BASE + 1)
        link.close()

        inst.run(c, 'rm -f %s/*.upk' % inst.DIR)
        report['cleanup_loaded_txt'] = inst.reload(c)
        report['journal_tail'] = inst.run(c, "journalctl -b -u tcp-image-server -n 400 --no-pager | "
                                             "grep -E '\\[prog\\]' | tail -40 || true")
    finally:
        c.close()

    report['pass'] = ok
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report['H_ai_ms']))
    print('PROGRAMS_BOARD_PASS' if ok else 'PROGRAMS_BOARD_FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
