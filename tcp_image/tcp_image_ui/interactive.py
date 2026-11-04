"""interactive.py —— 点击纠错（F2）：把“图像 + 先验掩膜 + 点击”编码进一帧 RGB 发给板子。

板子上跑的还是同一个 30 节点网络，只是用了另一套参数（模型库里的 echo_inter）。三个通道：

    R  原图（灰度图本来就是 R=G=B，取 R）
    G  先验掩膜：0 / 255。来自上一次的结果（第一步是自动分割的结果）
    B  有符号点击图：round(128 + 127·pos − 128·neg)，夹到 0..255
       pos / neg = 各正 / 负点的锥形 max(0, 1 − d / 40) 取最大，d 是 512 画布上的像素距离

板子在 128 档按 x[::4, ::4] 取样，锥形就变成训练时用的 128 尺度、半径 10 的锥形
（echo/train_inter.py 的 click_channel / cone，数学上完全相同）。

纯 Python、无 numpy，与 confidence.py 一致。
"""

import math

import proto

RADIUS = 40.0                     # 512 画布上的点击锥形半径（= 128 档的 10 像素）
W, H = proto.IMG_WIDTH, proto.IMG_HEIGHT


def click_plane(clicks):
    """clicks: [(x, y, positive)]（512 画布坐标）-> 512x512 字节，每像素一个 B 通道值。"""
    pos = {}
    neg = {}
    r = int(math.ceil(RADIUS))
    for x, y, positive in clicks:
        target = pos if positive else neg
        for py in range(max(0, int(y) - r), min(H, int(y) + r + 1)):
            dy = py - y
            row = py * W
            for px in range(max(0, int(x) - r), min(W, int(x) + r + 1)):
                c = 1.0 - math.sqrt(dy * dy + (px - x) ** 2) / RADIUS
                if c > 0 and c > target.get(row + px, 0.0):
                    target[row + px] = c
    out = bytearray(b"\x80" * (W * H))
    for k in set(pos) | set(neg):
        v = int(round(128 + 127 * pos.get(k, 0.0) - 128 * neg.get(k, 0.0)))
        out[k] = 0 if v < 0 else (255 if v > 255 else v)
    return bytes(out)


def prior_from_plane(plane, thr):
    """logit 偏移码平面（confidence.logit_plane）-> 先验掩膜（每像素 0 / 1）。"""
    lut = bytes(1 if v - proto.LOGIT_IMG_OFFSET >= thr else 0 for v in range(256))
    return plane.translate(lut)


def compose(image, prior, clicks):
    """原图 RGB（786432 字节）+ 先验（262144 字节 0/1）+ 点击 -> 要发给板子的 RGB。"""
    if len(image) != proto.IMG_TOTAL_BYTES or len(prior) != W * H:
        raise ValueError("compose: bad sizes")
    out = bytearray(proto.IMG_TOTAL_BYTES)
    out[0::3] = image[0::3]
    out[1::3] = prior.translate(bytes([0, 255] + [255] * 254))
    out[2::3] = click_plane(clicks)
    return bytes(out)


def compose_seq(image, prior, prev_image):
    """序列传播（F3，echo_seq）：R = 本帧，G = 上一步的结果 0/255，B = 上一步那一帧（取 R）。"""
    if len(image) != proto.IMG_TOTAL_BYTES or len(prev_image) != proto.IMG_TOTAL_BYTES or len(prior) != W * H:
        raise ValueError("compose_seq: bad sizes")
    out = bytearray(proto.IMG_TOTAL_BYTES)
    out[0::3] = image[0::3]
    out[1::3] = prior.translate(bytes([0, 255] + [255] * 254))
    out[2::3] = prev_image[0::3]
    return bytes(out)


class Session(object):
    """一张图上的一轮纠错：先验、点击、每一步的回传（用于撤销）。"""

    def __init__(self, prior, images, ai_stats):
        self.history = [(prior, [], images, ai_stats)]

    @property
    def prior(self):
        return self.history[-1][0]

    @property
    def clicks(self):
        return list(self.history[-1][1])

    def push(self, prior, clicks, images, ai_stats):
        self.history.append((prior, list(clicks), images, ai_stats))

    def undo(self):
        """回到上一步；返回该步的 (images, ai_stats)。已经在起点时返回 None。"""
        if len(self.history) <= 1:
            return None
        self.history.pop()
        return self.history[-1][2], self.history[-1][3]

    def reset(self):
        del self.history[1:]
        return self.history[0][2], self.history[0][3]


def _self_check():
    errs = []
    blank = click_plane([])
    if blank != b"\x80" * (W * H):
        errs.append("no clicks must give 128 everywhere")
    p = click_plane([(100, 200, True)])
    if p[200 * W + 100] != 255 or p[200 * W + 100 + 40] != 128 or p[200 * W + 100 + 20] != 192:
        errs.append("positive cone wrong: %d %d %d" % (p[200 * W + 100], p[200 * W + 140], p[200 * W + 120]))
    n = click_plane([(100, 200, False)])
    if n[200 * W + 100] != 0 or n[200 * W + 120] != 64:
        errs.append("negative cone wrong")
    img = bytes(range(256)) * (proto.IMG_TOTAL_BYTES // 256)
    prior = bytes([1]) * 10 + bytes(W * H - 10)
    c = compose(img, prior, [])
    if c[0::3] != img[0::3] or c[1] != 255 or c[31] != 0 or c[2] != 128:
        errs.append("compose channel layout wrong")
    prev = bytes(reversed(img))
    s = compose_seq(img, prior, prev)
    if s[0::3] != img[0::3] or s[2::3] != prev[0::3] or s[1] != 255 or s[31] != 0:
        errs.append("compose_seq channel layout wrong")
    return errs


SELF_CHECK_ERRORS = _self_check()
