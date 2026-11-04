# -*- coding: utf-8 -*-
"""Model library for the board program: several parameter sets, one bitstream.

Writes src/unet_models.h + src/unet_models.c. Every model has exactly the
same 30-node structure as the fundus network (only weights, shifts, INT8/INT16
ranges and branch multipliers differ), so the HLS kernels, the strip
scheduler and unet_net.h stay as they are.

  model 0  fundus   -> the existing src/unet_net_data.c globals (not regenerated)
  model 1  skin     -> E:/PLD/skin/deploy/r128          (ISIC 2016, 128 tier)
  model 2  echo_lv  -> E:/PLD/echo/deploy/lv_r128       (CAMUS LV cavity, 128 tier)
  model 3  echo_inter -> E:/PLD/echo/deploy/inter2_r128 (CAMUS LV click correction, 128 tier)
  model 4  echo_seq -> E:/PLD/echo/deploy/seq_none_ex      (CAMUS LV frame-to-frame propagation, 128 tier)
  model 5  reg_absdiff -> E:/PLD/echo/deploy/reg_absdiff   (F5: hand-built |R - G| per pixel, 512 tier)
  model 6-13 op_median3 / op_mean3 / op_sobel / op_gauss11 / op_unsharp / op_motion / op_change / op_vessel4
           -> E:/PLD/opcomp/deploy/<op>                      (② compiled operator programs, 128 tier)
  model 14 ct_spleen -> E:/PLD/ct/deploy/z3_r128          (⑤ MSD Task09 spleen CT, 2.5D z3 input, 128 tier)
  model 15 ct_spleen_roi -> E:/PLD/ct/deploy/roi_prior    (⑤ stage 2: 256 ROI window at 2x, coarse prior in G)
  model 16 echonet_lv -> E:/PLD/echonet/deploy/t3          (③ EchoNet-Dynamic LV, 112 padded to 128, temporal t3 input)

v5.27 (2026-11-03): heart + fundus vessels only -- models 1, 5-15 (REMOVED below) are emitted as empty slots.

Checks before writing:
  * each table is structurally identical to the fundus table;
  * regenerating the fundus table from its own vectors with this code gives
    exactly the arrays in src/unet_net_data.c (so the emitter is right);
  * the emitted C is parsed back and compared with network.txt / params/*.bin.

    python gen_unet_models.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(RX, 'src')
PLD = os.path.normpath(os.path.join(RX, '..', '..'))
FUNDUS_VEC = os.path.join(PLD, 'medical_fpga', 'deployment', 'unet_network', 'vectors')

# name, deploy dir (None = existing fundus globals), default shift, default threshold, ROI meaningful
MODELS = [
    ('fundus', None, 0, -3, 1),
    ('skin', os.path.join(PLD, 'skin', 'deploy', 'r128'), 2, 1, 0),
    ('echo_lv', os.path.join(PLD, 'echo', 'deploy', 'lv_r128'), 2, 1, 0),
    # F2 click correction: R frame, G prior mask, B signed click cones (see tcp_image_ui/interactive.py)
    ('echo_inter', os.path.join(PLD, 'echo', 'deploy', 'inter2_r128'), 2, 1, 0),
    # F3 sequence propagation: R frame t, G mask of the previous step, B 128 (see tcp_image_ui/sequence.py)
    ('echo_seq', os.path.join(PLD, 'echo', 'deploy', 'seq_none_ex'), 2, 1, 0),
    # F5 registration cost: hand-built weights, logit ~ |R - G| / 2 per pixel; 16 candidate shifts
    # tiled 4 x 4 in one 512 frame, the PS sums the tiles (FLAG_REG). Threshold 8 = |a - b| >= 16.
    ('reg_absdiff', os.path.join(PLD, 'echo', 'deploy', 'reg_absdiff'), 0, 8, 0),
    # ② compiled operator programs (E:/PLD/opcomp: expression -> weights of this same network, HLS bit-exact);
    # index >= 7 is reachable through compact frames (model byte), the 3-bit aiparam field stops at 6
] + [('op_' + n, os.path.join(PLD, 'opcomp', 'deploy', n), 2, 0, 0)
     for n in ('median3', 'mean3', 'sobel', 'gauss11', 'unsharp', 'motion', 'change', 'vessel4')] + [
    # ⑤ CT spleen (MSD Task09): R / G / B = CT slices z-5mm / z / z+5mm, W400/L40, host 4x4 box average -> 128
    # payload (compact frames only, index >= 7); see E:/PLD/ct and tcp_image_ui/ct_volume.py
    ('ct_spleen', os.path.join(PLD, 'ct', 'deploy', 'z3_r128'), 2, 1, 0),
    # ⑤ stage 2, ROI fine segmentation: a 256 x 256 window placed from the ct_spleen result, 2 x 2 box average -> 128;
    # R = slice, G = coarse mask in the window, B = mean of the neighbour slices (E:/PLD/ct/roi.py)
    ('ct_spleen_roi', os.path.join(PLD, 'ct', 'deploy', 'roi_prior'), 2, 1, 0),
    # ③ EchoNet-Dynamic LV: 112 x 112 grey frames zero-padded to 128, R / G / B = frames t-40ms / t / t+40ms;
    # logits cropped back to 112 on the host (E:/PLD/echonet, tcp_image_ui/echo_ws.py)
    ('echonet_lv', os.path.join(PLD, 'echonet', 'deploy', 't3'), 2, 1, 0),
]
# v5.27 (2026-11-03, heart-only build): these indices stay as empty slots ("removed", NULL tables) so every other
# index -- echonet_lv = 16 above all -- keeps its number; the server treats an empty slot as "not in this build".
REMOVED = {'skin', 'reg_absdiff', 'ct_spleen', 'ct_spleen_roi'} | {m[0] for m in MODELS if m[0].startswith('op_')}
FIELDS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w',
          'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')
KIND_ID = {'INPUT': 0, 'CONV': 1, 'ELT': 2}
C_ORDER = ('kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'out_c', 'relu', 'shift', 'qmin', 'qmax',
           'm0', 'm1', 'in_h', 'in_w', 'out_h', 'out_w')
STRUCT = ('kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'out_c', 'relu', 'in_h', 'in_w', 'out_h', 'out_w')


def load(vec):
    """network.txt + params/*.bin -> (rows in C_ORDER, param offsets, lengths, blob), as gen_unet_c.py."""
    nodes = []
    for line in open(os.path.join(vec, 'network.txt')):
        if not line.strip():
            continue
        n = dict(zip(FIELDS, line.split()))
        for k in FIELDS[4:]:
            n[k] = int(n[k])
        oh, ow, oc = n['h'], n['w'], n['ca']
        if n['kind'] == 'CONV':
            oc = n['cb']
            if n['mode'] == 2:
                oh, ow = 2 * oh, 2 * ow
        elif n['kind'] == 'ELT':
            if n['mode'] == 1:
                oc = n['ca'] + n['cb']
            elif n['mode'] == 2:
                oh, ow = oh // 2, ow // 2
        n.update(out_h=oh, out_w=ow, out_c=oc)
        nodes.append(n)
    idx = {n['name']: i for i, n in enumerate(nodes)}
    blob, off, ln, rows = bytearray(), [], [], []
    for n in nodes:
        data = b''
        if n['kind'] == 'CONV':
            data = open(os.path.join(vec, 'params', n['name'] + '.bin'), 'rb').read()
            assert len(data) % 8 == 0
        off.append(len(blob) if data else 0)
        ln.append(len(data))
        blob += data
        rows.append(dict(kind=KIND_ID[n['kind']], in0=idx.get(n['in0'], -1), in1=idx.get(n['in1'], -1),
                         mode=n['mode'], ca=n['ca'], cb=n['cb'], out_c=n['out_c'], relu=n['relu'],
                         shift=n['shift'], qmin=n['qmin'], qmax=n['qmax'], m0=n['m0'], m1=n['m1'],
                         in_h=n['h'], in_w=n['w'], out_h=n['out_h'], out_w=n['out_w']))
    return rows, off, ln, bytes(blob)


def parse_c(text, nodes_name, off_name, len_name, blob_name):
    body = re.search(nodes_name + r'\[UNET_NODES_LEN\] = \{(.*?)\n\};', text, re.S).group(1)
    rows = [dict(zip(C_ORDER, map(int, m.split(',')))) for m in re.findall(r'\{\s*([-\d,\s]+?)\s*\}', body)]
    arr = lambda nm: [int(v) for v in re.search(nm + r'\[UNET_NODES_LEN\] = \{\s*(.*?)\s*\};', text, re.S)
                      .group(1).replace(' ', '').replace('\n', '').split(',') if v]
    blob_txt = re.search(blob_name + r'\[\d+u?\][^{]*\{(.*?)\};', text, re.S).group(1)
    return rows, arr(off_name), arr(len_name), bytes(int(b, 16) for b in re.findall(r'0x([0-9A-F]{2})', blob_txt))


def emit_model(tag, rows, off, ln, blob):
    c = ['/* ---- model "%s" ---- */' % tag,
         'static const unet_node_t nodes_%s[UNET_NODES_LEN] = {' % tag]
    for r in rows:
        c.append('    { ' + ', '.join(str(r[k]) for k in C_ORDER) + ' },')
    c += ['};',
          'static const uint32_t param_off_%s[UNET_NODES_LEN] = {' % tag,
          '    ' + ', '.join(str(v) for v in off), '};',
          'static const uint32_t param_len_%s[UNET_NODES_LEN] = {' % tag,
          '    ' + ', '.join(str(v) for v in ln), '};',
          '/* 16-byte aligned: copied into BRAM 32 bits at a time */',
          'static const uint8_t params_%s[%du] __attribute__((aligned(16))) = {' % (tag, max(len(blob), 1))]
    for i in range(0, len(blob), 16):
        c.append('    ' + ' '.join('0x%02X,' % b for b in blob[i:i + 16]))
    c += ['};', '']
    return c


def main():
    fundus_c = open(os.path.join(SRC, 'unet_net_data.c'), encoding='utf-8').read()
    ref = parse_c(fundus_c, 'unet_nodes', 'unet_param_off', 'unet_param_len', 'unet_params')
    mine = load(FUNDUS_VEC)
    assert ref[0] == mine[0] and ref[1] == mine[1] and ref[2] == mine[2] and ref[3] == mine[3], \
        'emitter disagrees with gen_unet_c.py on the fundus table'
    print('[gen] fundus table regenerated identically from %s' % FUNDUS_VEC)

    body, table = [], []
    for i, (tag, vec, shift, thr, roi_ok) in enumerate(MODELS):
        if vec is None:
            table.append('    { "%s", unet_nodes, unet_param_off, unet_param_len, unet_params, %d, %d, %d },'
                         % (tag, shift, thr, roi_ok))
            continue
        if tag in REMOVED:
            table.append('    { "removed", NULL, NULL, NULL, NULL, 0, 0, 0 },   /* was %s */' % tag)
            print('[gen] model %d %-8s removed (empty slot)' % (i, tag))
            continue
        rows, off, ln, blob = load(vec)
        assert len(rows) == len(ref[0]) == 30
        diff = [j for j, (a, b) in enumerate(zip(rows, ref[0])) if any(a[k] != b[k] for k in STRUCT)]
        assert not diff, '%s: structure differs from fundus at nodes %s' % (tag, diff)
        assert len(blob) == len(ref[3]), '%s: parameter blob size differs' % tag
        body += emit_model(tag, rows, off, ln, blob)
        table.append('    { "%s", nodes_%s, param_off_%s, param_len_%s, params_%s, %d, %d, %d },'
                     % (tag, tag, tag, tag, tag, shift, thr, roi_ok))
        print('[gen] model %d %-8s <- %s  (shift %d, thr %d, roi %d)' % (i, tag, vec, shift, thr, roi_ok))

    h = ['/* AUTO-GENERATED by ai/net/gen_unet_models.py -- DO NOT EDIT. */',
         '#ifndef UNET_MODELS_H', '#define UNET_MODELS_H', '',
         '#include <stddef.h>', '#include <stdint.h>', '#include "unet_net.h"', '',
         '/* One bitstream, several parameter sets. All models share the 30-node',
         ' * structure of unet_net.h; only weights / shifts / ranges / multipliers differ. */',
         'typedef struct {',
         '    const char        *name;          /* short id, e.g. "fundus" */',
         '    const unet_node_t *nodes;         /* UNET_NODES_LEN entries */',
         '    const uint32_t    *param_off;     /* byte offset of each CONV node in params */',
         '    const uint32_t    *param_len;',
         '    const uint8_t     *params;',
         '    uint8_t            default_shift; /* working size 512 >> shift if the client does not choose */',
         '    int8_t             default_thr;   /* logit >= thr marks the target */',
         '    uint8_t            roi_ok;        /* 1 = full-resolution model, ROI inference meaningful */',
         '} unet_model_t;', '',
         '/* v5.27: an index removed from this build is an empty slot (nodes == NULL); its number is not reused. */',
         '#define UNET_MODEL_PRESENT(k) (unet_models[(k)].nodes != NULL)', '',
         '#define UNET_MODELS_COUNT %du' % len(MODELS),
         'extern const unet_model_t unet_models[UNET_MODELS_COUNT];', '',
         '#endif /* UNET_MODELS_H */']
    c = ['/* AUTO-GENERATED by ai/net/gen_unet_models.py -- DO NOT EDIT. */',
         '#include "unet_models.h"', ''] + body + \
        ['const unet_model_t unet_models[UNET_MODELS_COUNT] = {'] + table + ['};']
    open(os.path.join(SRC, 'unet_models.h'), 'w', encoding='utf-8', newline='\n').write('\n'.join(h) + '\n')
    text = '\n'.join(c) + '\n'
    open(os.path.join(SRC, 'unet_models.c'), 'w', encoding='utf-8', newline='\n').write(text)

    for tag, vec, *_ in MODELS[1:]:                 # parse back what was written
        if tag in REMOVED:
            continue
        got = parse_c(text, 'nodes_' + tag, 'param_off_' + tag, 'param_len_' + tag, 'params_' + tag)
        assert got == load(vec), '%s: emitted C does not round-trip' % tag
    print('[gen] src/unet_models.h + src/unet_models.c written, %d models, round-trip OK' % len(MODELS))


if __name__ == '__main__':
    sys.exit(main())
