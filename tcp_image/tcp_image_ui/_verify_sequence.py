# -*- coding: utf-8 -*-
"""_verify_sequence.py —— F3 序列跟踪的上位机侧自证（不连板）。

用“假板子”（本机跑与板端逐值相同的整数模型，echo/ 下的 int8_reference）代替网络，
把 sequence.Tracker 在示例序列上整段跑一遍，检查：

  1. 载入：文件夹里每帧经 imageio.load_image 拉伸后，[::4, ::4] 等于离线缓存用的
     common.host_stretch(灰度)[::4, ::4]（界面与离线评测的输入逐字节相同）；
  2. 每一步发出去的“RGB”（compose / compose_seq）在板端取样后，等于 seqeval.encode
     拼出的 128 输入；每一步的掩膜等于离线 seqeval.propagate 的结果；
  3. AreaMapper 的原图面积等于 seg_metrics 的 pred_area_px 口径（NEAREST 行列映射）；
  4. 点击纠正：在最后一帧上点一次，发出去的输入等于 F2 的 click_maps 编码；撤销能回到原值；
  5. 往返循环的播放顺序 0..n-1, n-2..0, 1.. 正确。

用 E:\\PLD\\skin\\.venv 的 python 跑（要 numpy / torch）：
    E:\\PLD\\skin\\.venv\\Scripts\\python.exe _verify_sequence.py [--seq-model 4|3]
打印一律用英文。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ECHO = os.path.normpath(os.path.join(HERE, "..", "..", "echo"))
sys.path.insert(0, HERE)
sys.path.insert(0, ECHO)

import numpy as np                      # noqa: E402
import torch                            # noqa: E402
from PIL import Image                   # noqa: E402

import confidence                       # noqa: E402
import interactive                      # noqa: E402
import proto                            # noqa: E402
import sequence                         # noqa: E402
from common import RESULTS, host_stretch, nearest_map   # noqa: E402
from int8_reference import run_integer  # noqa: E402
from seqeval import encode              # noqa: E402
from train_inter import click_maps      # noqa: E402

FAILS = []


def check(label, ok, extra=""):
    print("%-58s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class FakeBoard(object):
    """round_trip() 的替身：按 aiparam 的模型号跑整数模型，回 logit / mask 图和统计块。"""

    def __init__(self, tags):
        self.models = {}
        for idx, tag in tags.items():
            d = RESULTS / "quant" / tag
            plan = json.loads((d / "quant_plan.json").read_text(encoding="utf-8"))
            with np.load(d / "parameters.npz", allow_pickle=False) as z:
                self.models[idx] = (plan, {k: z[k] for k in z.files})
        self.sent = []

    def round_trip(self, image, mode=0, flags=0, aiparam=None, **kw):
        model = ((aiparam & proto.AIPARAM_MODEL_MASK) >> proto.AIPARAM_MODEL_SHIFT) - 1    # 字段 = 下标 + 1
        x128 = np.frombuffer(image, np.uint8).reshape(512, 512, 3)[::4, ::4]
        self.sent.append((model, x128.copy()))
        plan, arrays = self.models[model]
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(x128)).permute(2, 0, 1).unsqueeze(0)
            logit = run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int64)
        up = np.repeat(np.repeat(logit, 4, 0), 4, 1)
        plane = np.clip(up + proto.LOGIT_IMG_OFFSET, 0, 255).astype(np.uint8)
        logit_img = np.repeat(plane[..., None], 3, -1).tobytes()
        mask_img = confidence.mask_rgb(plane.tobytes(), 1)
        return {"logit": logit_img, "mask": mask_img}, {"total_ms": 0.0}, {}, {"model": model, "ai_res": 128, "ai_us": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq-model", type=int, default=None, help="propagation model index (default: proto)")
    ap.add_argument("--seq-tag", default="seq_none_ex")
    ap.add_argument("--folder", default=os.path.join(ECHO, "samples", "seq_patient0027_4CH"))
    args = ap.parse_args()
    auto = proto.MODELS[2]
    seq_idx = args.seq_model if args.seq_model is not None else auto.get("seq", auto["inter"])
    seq_m = proto.MODELS[seq_idx] if seq_idx < len(proto.MODELS) else dict(index=seq_idx, thr=1, res=128, enc="prev")
    enc = seq_m.get("enc", "none")
    tags = {2: "lv_r128", 3: "inter2_r128"}
    if seq_idx >= 4:
        tags[seq_idx] = args.seq_tag
    board = FakeBoard(tags)

    frames, info = sequence.load_sequence(args.folder)
    names = sorted(n for n in os.listdir(args.folder) if n.endswith(".png") and not n.endswith("_gt.png"))
    grey = [np.array(Image.open(os.path.join(args.folder, n))) for n in names]
    x128 = [host_stretch(g)[::4, ::4] for g in grey]
    check("1 loaded frames = host_stretch[::4, ::4] (%d frames)" % len(frames),
          all(np.array_equal(np.frombuffer(f, np.uint8).reshape(512, 512, 3)[::4, ::4, 0], x) for f, x in zip(frames, x128)))
    h, w = grey[0].shape
    mapper = sequence.AreaMapper(w, h, info.get("spacing_mm"))
    tr = sequence.Tracker(frames, auto, seq_m, proto.MODELS[auto["inter"]], mapper)

    def drive(req_fn, accept_fn):
        res = req_fn()
        imgs, tim, _, st = board.round_trip(res[1], aiparam=proto.make_aiparam(0, res[2]["thr"], 128, res[2]["index"]))
        plane = confidence.logit_plane(imgs["logit"])
        return accept_fn(res, plane, imgs, st)

    while True:
        req = tr.next_request()
        if req is None:
            break
        step, image, model = req
        imgs, tim, _, st = board.round_trip(image, aiparam=proto.make_aiparam(0, model["thr"], 128, model["index"]))
        tr.accept(step, confidence.logit_plane(imgs["logit"]), model["thr"], imgs["mask"], st["ai_us"], tim["total_ms"])
    check("  every step verified (local mask == board mask image)", all(s.verified for s in tr.steps))

    # offline reference: same integer models, seqeval encoding, F1 at frame 0
    def predict(model_idx):
        plan, arrays = board.models[model_idx]

        def p(rgb):
            with torch.inference_mode():
                q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(0, 3, 1, 2)
                return run_integer(plan, arrays, q)[0][:, 0].numpy() >= 1
        return p
    ref = [predict(2)(np.repeat(x128[0][None, ..., None], 3, -1))[0]]
    sent_ok = [np.array_equal(board.sent[0][1], np.repeat(x128[0][..., None], 3, -1)) and board.sent[0][0] == 2]
    for t in range(1, len(x128)):
        rgb = encode(enc, x128[t], ref[-1], x128[t - 1])
        sent_ok.append(board.sent[t][0] == seq_idx and np.array_equal(board.sent[t][1], rgb))
        ref.append(predict(seq_idx)(rgb[None])[0])
    check("2 sent inputs == seqeval.encode('%s') (model %d)" % (enc, seq_idx), all(sent_ok))
    got = [np.frombuffer(s.mask, np.uint8).reshape(512, 512)[::4, ::4].astype(bool) for s in tr.steps]
    check("  step masks == offline propagation", all(np.array_equal(a, b) for a, b in zip(got, ref)))

    rows = np.bincount(nearest_map(512, h), minlength=512)
    cols = np.bincount(nearest_map(512, w), minlength=512)
    want = [int(rows @ np.repeat(np.repeat(m, 4, 0), 4, 1).astype(np.int64) @ cols) for m in ref]
    check("3 AreaMapper == rows @ mask @ cols", [s.area for s in tr.steps] == want, str(want[:3]))
    kf = tr.keyframes()
    print("   keyframes: ED frame %d (%d px), ES frame %d (%d px), FAC %.3f" % (kf[0], kf[1], kf[2], kf[3], kf[4]))

    before = tr.steps[-1].mask
    ys, xs = np.nonzero(got[-1])
    cy, cx = int(ys.mean()) * 4, int(xs.mean()) * 4
    clicks = [(cx, cy, True)]
    image, model = tr.click_request(clicks)
    sent = np.frombuffer(image, np.uint8).reshape(512, 512, 3)[::4, ::4]
    want_b = click_maps([(cy // 4, cx // 4, True)])
    check("4 click input: R frame, G current mask, B == click_maps",
          np.array_equal(sent[..., 0], x128[-1]) and np.array_equal(sent[..., 1] > 0, got[-1])
          and np.array_equal(sent[..., 2], want_b) and model["index"] == auto["inter"])
    imgs, tim, _, st = board.round_trip(image, aiparam=proto.make_aiparam(0, 1, 128, model["index"]))
    tr.accept_click(clicks, confidence.logit_plane(imgs["logit"]), 1, imgs["mask"], 0, 0)
    check("  after click: kind fix, verified", tr.steps[-1].kind == "fix" and tr.steps[-1].verified)
    check("  undo restores the propagated mask", tr.undo_click() and tr.steps[-1].mask == before
          and tr.steps[-1].kind == "prop")

    pp = sequence.Tracker(frames, auto, seq_m, proto.MODELS[3], mapper, pingpong=True)
    n = len(frames)
    order = [pp.frame_at(k) for k in range(3 * n)]
    want_o = list(range(n)) + list(range(n - 2, -1, -1)) + list(range(1, n))
    check("5 ping-pong order", order[:len(want_o)] == want_o)
    print("SEQUENCE_UI_SELF_CHECK_PASS" if not FAILS else "SEQUENCE_UI_SELF_CHECK_FAIL %s" % FAILS)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
