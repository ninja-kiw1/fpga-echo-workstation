# -*- coding: utf-8 -*-
"""A1 estimate: strip count + words moved at BRAM 8192 vs 16384 words.
Reads the 512-resolution network.txt + params sizes; mirrors unet_ai.c strip math.
"""
import os, sys

VEC = sys.argv[1] if len(sys.argv) > 1 else \
    r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_network/vectors"
LANES = 8
WORD = 16
FIELDS = ('name','kind','in0','in1','mode','ca','cb','h','w',
          'relu','shift','qmin','qmax','m0','m1')

def groups(c): return (c + LANES - 1) // LANES
def ceil_div(a,b): return -(-a//b)

nodes = []
for line in open(os.path.join(VEC, "network.txt")):
    if not line.strip(): continue
    f = line.split()
    n = dict(zip(FIELDS, f))
    for k in FIELDS[4:]: n[k] = int(n[k])
    oh, ow, oc = n["h"], n["w"], n["ca"]
    if n["kind"] == "CONV":
        oc = n["cb"]
        if n["mode"] == 2: oh, ow = 2*oh, 2*ow
    elif n["kind"] == "ELT":
        if n["mode"] == 1: oc = n["ca"] + n["cb"]
        elif n["mode"] == 2: oh, ow = oh//2, ow//2
    n.update(out_h=oh, out_w=ow, out_c=oc)
    nodes.append(n)

idx = {n["name"]: i for i, n in enumerate(nodes)}

def param_bytes(name):
    p = os.path.join(VEC, "params", name + ".bin")
    try: return os.path.getsize(p)
    except OSError: return None

# per-node: rin/rout in WORDS (16B units), P in words
def dims(i):
    n = nodes[i]
    if n["kind"] == "INPUT":
        return (n["out_w"]*groups(n["out_c"]), 0, 0)   # not used as a conv
    A = nodes[idx[n["in0"]]]
    w_in = A["out_w"]; g_in = groups(A["out_c"])
    w_out = n["out_w"]; g_out = groups(n["out_c"])
    rin = w_in*g_in; rout = w_out*g_out
    pb = param_bytes(n["name"]) if n["kind"] == "CONV" else 0
    P = ceil_div(pb or 0, WORD)
    if n["kind"] == "CONV" and pb is None:
        P = None  # unknown
    return (rin, rout, P)

def strip(i, BR):
    n = nodes[i]; rin, rout, P = dims(i)
    if P is None: P = 0
    H = n["out_h"]
    if n["kind"] == "CONV" and n["mode"] == 0:      # 3x3
        avail = BR - P
        S = (avail - 2*rin)//(rin+rout)
        if S < 1: return (1, None)
        S = min(S, H)
        return (S, ceil_div(H, S))
    if n["kind"] == "CONV" and n["mode"] == 1:      # 1x1
        S = (BR-P)//(rin+rout); S = max(1, min(S, H)); return (S, ceil_div(H,S))
    if n["kind"] == "CONV" and n["mode"] == 2:      # tconv: n in -> 2n out
        n_ = (BR-P)//(rin+2*rout); n_ = max(1, n_)
        S = 2*n_
        if S % 2: S -= 1
        S = max(2, min(S, H))
        return (S, ceil_div(H, S))
    if n["kind"] == "ELT":
        B = nodes[idx[n["in1"]]] if n["in1"] != "-" else None
        rin_b = (B["out_w"]*groups(B["out_c"])) if (B and n["mode"] in (0,1)) else 0
        if n["mode"] == 2:      # maxpool 2S in -> S out
            S = BR//(2*rin + rout); S = max(1, min(S, H)); return (S, ceil_div(H,S))
        S = BR//(rin + rin_b + rout); S = max(1, min(S, H)); return (S, ceil_div(H,S))
    return (0, 0)

def moved(i, BR):
    """words written to BRAM (in) + read back (out) + params copies."""
    n = nodes[i]
    if n["kind"] == "INPUT": return (0, 0, 0)
    rin, rout, P = dims(i)
    P = P or 0
    S, nstrip = strip(i, BR)
    if not nstrip: return (0, 0, 0)
    H = n["out_h"]
    # in rows copied
    if n["kind"] == "CONV" and n["mode"] == 0:
        rows = H + 2*nstrip            # each strip copies S(+2 headroom) rows
    elif n["kind"] == "CONV" and n["mode"] == 2:
        rows = (H//2) + nstrip         # feeds n rows; approx
    elif n["kind"] == "ELT" and n["mode"] == 2:
        rows = 2*H
    else:
        rows = H
    iw = rows*rin
    B = nodes[idx[n["in1"]]] if n["in1"] != "-" else None
    if n["kind"] == "ELT" and n["mode"] in (0,1) and B:
        iw += rows*(B["out_w"]*groups(B["out_c"]))
    ow = H*rout
    pw = P*nstrip
    return (iw, ow, pw)

for BR in (8192, 16384):
    ti = to = tp = 0
    print("=== BRAM %d words (%d KB) ===" % (BR, BR*16//1024))
    worst = []
    for i,n in enumerate(nodes):
        iw, ow, pw = moved(i, BR)
        ti += iw; to += ow; tp += pw
        if n["kind"] != "INPUT":
            S, ns = strip(i, BR)
            worst.append((ns or 0, n["name"], S, ns))
    worst.sort(reverse=True)
    print("  top strips:", ", ".join("%s x%d" % (w[1], w[3]) for w in worst[:5]))
    print("  TOTAL in=%d out=%d params=%d  sum=%d words = %.1f MB"
          % (ti, to, tp, ti+to+tp, (ti+to+tp)*16/1e6))
print("done")
