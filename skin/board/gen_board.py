"""Board-side C for the ISIC model, coexisting with the fundus build (plan A).

Plan A = two programs. Nothing in tcp_image_rx/src/ is modified:
  1. tcp_image_rx/ai/net/gen_unet_c.py (unchanged, run as a tool) turns
     skin/deploy/<tag>/{network.txt,params/} into board/gen/unet_net.{h,c};
  2. the generated header must equal src/unet_net.h except comments and the
     unused UNET_THRESHOLD_DEFAULT macro (same node count, same I/O size);
  3. the generated table/blob is parsed back and compared with network.txt and
     params/*.bin, and its structural fields with the fundus table;
  4. board/stage/ = copy of src/ + makefile with the two generated files
     replaced. On the board it builds `tcp_image_server_skin`.

    python board/gen_board.py --tag r128
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIN = HERE.parent
RX = SKIN.parent / 'tcp_image' / 'tcp_image_rx'
GEN_TOOL = RX / 'ai' / 'net' / 'gen_unet_c.py'
FIELDS = ('kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'out_c', 'relu', 'shift', 'qmin', 'qmax',
          'm0', 'm1', 'in_h', 'in_w', 'out_h', 'out_w')
STRUCT = ('kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'out_c', 'relu', 'in_h', 'in_w', 'out_h', 'out_w')


def parse_data_c(path):
    text = path.read_text(encoding='utf-8')
    nodes_txt = re.search(r'unet_nodes\[UNET_NODES_LEN\] = \{(.*?)\n\};', text, re.S).group(1)
    nodes = [dict(zip(FIELDS, map(int, m.split(','))))
             for m in re.findall(r'\{\s*([-\d,\s]+?)\s*\}', nodes_txt)]
    arr = lambda name: [int(v) for v in re.search(name + r'\[UNET_NODES_LEN\] = \{\s*(.*?)\s*\};', text, re.S)
                        .group(1).replace(' ', '').split(',')]
    blob_txt = re.search(r'unet_params\[\d+u\][^{]*\{(.*?)\};', text, re.S).group(1)
    blob = bytes(int(b, 16) for b in re.findall(r'0x([0-9A-F]{2})', blob_txt))
    return nodes, arr('unet_param_off'), arr('unet_param_len'), blob


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    deploy = SKIN / 'deploy' / args.tag
    manifest = json.loads((deploy / 'manifest.json').read_text(encoding='utf-8'))
    qt, res = manifest['output_integer_threshold_ge'], manifest['deploy_resolution']
    gen = HERE / 'gen'
    gen.mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(GEN_TOOL), str(deploy), str(gen)], check=True)

    h = (gen / 'unet_net.h').read_text(encoding='utf-8')
    h = h.replace('/* Vessel criterion: a pixel is vessel when logit >= UNET_THRESHOLD_DEFAULT. */',
                  f'/* ISIC lesion model ({args.tag}): lesion when logit >= UNET_THRESHOLD_DEFAULT (p > 0.5).\n'
                  f' * Only a default; the host sends the threshold in aiparam bits[15:8]. Run at --ai-res {res}. */')
    h = h.replace('#define UNET_THRESHOLD_DEFAULT (-3)', f'#define UNET_THRESHOLD_DEFAULT ({qt})')
    (gen / 'unet_net.h').write_text(h, encoding='utf-8')

    # 2) header compatibility with the fundus build
    def strip(s):   # code tokens only: no comments, whitespace or line-ending differences
        s = re.sub(r'/\*.*?\*/', ' ', s, flags=re.S)
        return [' '.join(l.split()) for l in s.splitlines() if l.strip() and 'UNET_THRESHOLD_DEFAULT' not in l]
    fundus_h = (RX / 'src' / 'unet_net.h').read_text(encoding='utf-8')
    assert strip(h) == strip(fundus_h), 'generated header differs from src/unet_net.h beyond comments'

    # 3) round trip: C arrays == network.txt + params/*.bin; structure == fundus
    nodes, off, ln, blob = parse_data_c(gen / 'unet_net_data.c')
    table = [l.split() for l in (deploy / 'network.txt').read_text(encoding='ascii').splitlines() if l.strip()]
    assert len(nodes) == len(table) == 30
    for n, t, o, l in zip(nodes, table, off, ln):
        name, kind = t[0], t[1]
        assert (n['mode'], n['ca'], n['cb'], n['relu'], n['shift'], n['qmin'], n['qmax'], n['m0'], n['m1'],
                n['in_h'], n['in_w']) == tuple(int(v) for v in (t[4], t[5], t[6], t[9], t[10], t[11], t[12],
                                                                    t[13], t[14], t[7], t[8])), name
        if kind == 'CONV':
            assert blob[o:o + l] == (deploy / 'params' / f'{name}.bin').read_bytes(), name
        else:
            assert l == 0, name
    fundus_nodes, _, _, fundus_blob = parse_data_c(RX / 'src' / 'unet_net_data.c')
    struct_diff = [i for i, (a, b) in enumerate(zip(nodes, fundus_nodes)) if any(a[f] != b[f] for f in STRUCT)]
    assert not struct_diff, f'structure differs from the fundus table at nodes {struct_diff}'
    assert len(blob) == len(fundus_blob), 'parameter blob size differs'
    changed = {f: sum(a[f] != b[f] for a, b in zip(nodes, fundus_nodes)) for f in ('shift', 'qmin', 'qmax', 'm0', 'm1')}
    int16 = [t[0] for t in table if int(t[12]) > 127 and t[1] != 'INPUT']
    fundus_int16 = [i for i, n in enumerate(fundus_nodes) if n['qmax'] > 127 and n['kind'] != 0]

    # 4) staging tree for the board build
    stage = HERE / 'stage'
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(RX / 'src', stage / 'src')
    shutil.copy2(RX / 'makefile', stage / 'makefile')
    for f in ('unet_net.h', 'unet_net_data.c'):
        shutil.copy2(gen / f, stage / 'src' / f)
    report = dict(tag=args.tag, resolution=res, threshold_ge=qt, nodes=len(nodes), param_bytes=len(blob),
                  header_compatible=True, structure_identical_to_fundus=True,
                  fields_changed_vs_fundus=changed, int16_nodes=int16,
                  fundus_int16_node_indices=fundus_int16,
                  fundus_files_modified=False)
    (HERE / 'gen_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
