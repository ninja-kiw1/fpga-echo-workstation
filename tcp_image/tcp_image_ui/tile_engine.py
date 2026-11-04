# -*- coding: utf-8 -*-
"""tile_engine.py —— 大图分块推理引擎（部分 C）：任意大小的图、任意框、任意放大倍数，
用固定的 512 PL U-Net 引擎分块算，拼起来与"整幅图一次推理"逐位相同。

这一层不碰界面（tkinter）也不碰网络细节，上位机窗口、板端网页 / DP 前端、验收脚本都调它（C3）。

概念
  原图    任意尺寸的 RGB（HRF 3504x2336、STARE 700x605……）。
  层      原图按比例 s 缩放（Pillow LANCZOS，与 imageio 拉伸 512 帧同一种滤波），右 / 下补黑到 8 的倍数。
          放大倍数 m：s = m * W1X / 原图宽——1 倍 = 模型尺度 = 原图宽缩到 W1X = 600 像素（眼底视野约占图宽 93%，
          STARE / HRF / DRIVE 都是，所以 ≈ 视野宽 560；由 STARE 尺度扫描定，见 E:/PLD/hrf/README.md）。
          层图预先在 PC 上做好放到板上 /data。
  框      层坐标里的 (x, y, w, h)。
  块      512 以内、起点 4 对齐、尺寸 8 的倍数的裁剪窗口；不贴图像边的那几条边各留 MARGIN = 24 像素真实邻域
          （ai/net/roi_exactness.py：裁剪边界的影响最深正好 22 像素）。每个块只取它的"自有区"（各块的自有区
          不重叠、正好铺满框），所以拼接结果 = 整图推理的结果。
  只要一块就够时（框 + 边距 <= 512），块就是老 ROI 的裁剪窗口（proto.roi_crop / 板端 ai_view_roi_crop
  同一规则）：512 帧上 1 倍框选 = 原来的 ROI 推理，逐位相同。

板端同一规划写在 tcp_image_rx/src/job_tile.c（tile_plan_axis），两份由 E:/PLD/hrf/c2_plan_compare.py 对照。
"""
import math

MARGIN = 24        # 每边真实邻域（理论需要 22）
TILE = 512         # PL 卷积核一行最多 512
ALIGN = 4          # 两级 2x2 池化：块起点 4 对齐
SIZE_ALIGN = 8     # 块尺寸 8 的倍数（最深层宽高仍是偶数）
STEP = TILE - 2 * MARGIN   # 多块时相邻块起点间距 464（8 的倍数，起点从 8 对齐开始就一直 8 对齐）
FOV_THR = 20       # 视野：(77R+150G+29B)>>8 > 20（= 板端 VM_FOV_THR）
OUTSIDE_LOGIT = -128

W1X = 600          # 1 倍 = 原图宽缩到 600 像素（STARE 尺度扫描的结果，见 hrf/README.md；改了要同步那里）


def ceil_to(v, a):
    return -(-v // a) * a


# ---------------------------------------------------------------------------------------------
# 规划
# ---------------------------------------------------------------------------------------------
def plan_axis(a, n, full, margin=MARGIN, tile=TILE):
    """一个方向：框 [a, a+n) 在 [0, full) 里（full 是 8 的倍数）-> [(块起点, 块长, 自有区起点, 自有区终点)]。
    一块够用时 = 老 ROI 规则（外扩 margin、起点向下对齐 4、长度向上取 8、越界回挪）；
    否则从 floor8(a - margin) 起每隔 STEP 放一块 512，最后一块只取需要的长度（越界回挪），自有区首尾相接。"""
    if not (0 <= a and n >= 1 and a + n <= full and full % SIZE_ALIGN == 0):
        raise ValueError("bad axis: a=%d n=%d full=%d" % (a, n, full))
    lo = max(0, a - margin)
    hi = min(full, a + n + margin)
    s = lo // ALIGN * ALIGN
    size = ceil_to(hi - s, SIZE_ALIGN)
    if size <= tile:
        if s + size > full:
            s = full - size
        return [(s, size, a, a + n)]
    out = []
    t = lo // SIZE_ALIGN * SIZE_ALIGN
    own0 = a
    while True:
        size = min(tile, ceil_to(hi - t, SIZE_ALIGN))
        if t + size > full:
            t = full - size
        core_end = t + size - (margin if t + size < full else 0)
        own1 = min(a + n, core_end)
        out.append((t, size, own0, own1))
        if own1 >= a + n:
            return out
        own0 = own1
        t += STEP


class Tile(object):
    __slots__ = ("index", "tx", "ty", "tw", "th", "ox", "oy", "ow", "oh", "skip", "score")

    def __init__(self, index, tx, ty, tw, th, ox, oy, ow, oh):
        self.index, self.tx, self.ty, self.tw, self.th = index, tx, ty, tw, th
        self.ox, self.oy, self.ow, self.oh = ox, oy, ow, oh
        self.skip = False
        self.score = 0.0

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "Tile(%d: %d,%d %dx%d own %d,%d %dx%d%s)" % (self.index, self.tx, self.ty, self.tw, self.th,
                                                          self.ox, self.oy, self.ow, self.oh,
                                                          " skip" if self.skip else "")


def plan(box, level_w, level_h, margin=MARGIN, tile=TILE):
    """框 (x, y, w, h) -> [Tile]，行优先（板端同一顺序编号）。"""
    x, y, w, h = [int(v) for v in box]
    xs = plan_axis(x, w, level_w, margin, tile)
    ys = plan_axis(y, h, level_h, margin, tile)
    out = []
    for (ty, th, oy0, oy1) in ys:
        for (tx, tw, ox0, ox1) in xs:
            out.append(Tile(len(out), tx, ty, tw, th, ox0, oy0, ox1 - ox0, oy1 - oy0))
    return out


def fov_mask(rgb):
    """视野 = 亮度 (77R+150G+29B)>>8 > 20（板端 vm_fov_from_rgb 同一公式）。rgb: (h, w, 3) uint8。"""
    import numpy as np
    r, g, b = (rgb[..., i].astype(np.int32) for i in range(3))
    return ((77 * r + 150 * g + 29 * b) >> 8) > FOV_THR


def mark_fov_skip(tiles, level_rgb=None, fov=None):
    """自有区里一个视野像素都没有的块标记 skip（板端同一判据：按亮度现算）。返回跳过的块数。"""
    if fov is None:
        fov = fov_mask(level_rgb)
    n = 0
    for t in tiles:
        t.skip = not bool(fov[t.oy:t.oy + t.oh, t.ox:t.ox + t.ow].any())
        n += t.skip
    return n


def costs(tiles):
    """计算量账：算的像素、自有像素、halo 开销、有效像素比（跳过的块不算）。"""
    comp = sum(t.tw * t.th for t in tiles if not t.skip)
    own = sum(t.ow * t.oh for t in tiles if not t.skip)
    own_all = sum(t.ow * t.oh for t in tiles)
    return dict(tiles=len(tiles), run=sum(1 for t in tiles if not t.skip),
                skipped=sum(1 for t in tiles if t.skip), computed_px=comp, own_px=own, box_px=own_all,
                halo_overhead=(comp / own - 1.0) if own else 0.0, useful_ratio=(own / comp) if comp else 0.0)


# ---------------------------------------------------------------------------------------------
# 拼接
# ---------------------------------------------------------------------------------------------
def tile_rgb(level_rgb, t):
    return level_rgb[t.ty:t.ty + t.th, t.tx:t.tx + t.tw]


def own_of(t, tile_logits):
    """块的整幅输出 (th, tw) -> 自有区 (oh, ow)。"""
    return tile_logits[t.oy - t.ty:t.oy - t.ty + t.oh, t.ox - t.tx:t.ox - t.tx + t.ow]


def stitch(box, tiles, owns):
    """owns[i] = 第 i 块自有区的 logit（或 None = 跳过）-> 框内 (h, w) int8，跳过处 = -128。"""
    import numpy as np
    x, y, w, h = box
    out = np.full((h, w), OUTSIDE_LOGIT, np.int8)
    for t in tiles:
        o = owns[t.index]
        if o is None:
            continue
        out[t.oy - y:t.oy - y + t.oh, t.ox - x:t.ox - x + t.ow] = o
    return out


def run_tiles(level_rgb, box, runner, skip_fov=True, order=None, on_tile=None):
    """在 PC 上按同一规划跑（runner(rgb_tile) -> int8 logits，可以是 NumPy 逐位参考或别的）。
    返回 (框内 logit, tiles)。on_tile(tile, own_logits) 每算完一块调一次（逐块填热图）。"""
    H, W = level_rgb.shape[:2]
    tiles = plan(box, W, H)
    if skip_fov:
        mark_fov_skip(tiles, level_rgb)
    owns = [None] * len(tiles)
    for i in (order if order is not None else range(len(tiles))):
        t = tiles[i]
        if t.skip:
            continue
        owns[i] = own_of(t, runner(tile_rgb(level_rgb, t)))
        if on_tile is not None:
            on_tile(t, owns[i])
    return stitch(box, tiles, owns), tiles


# ---------------------------------------------------------------------------------------------
# 层（金字塔）
# ---------------------------------------------------------------------------------------------
def fov_width(rgb_or_fov):
    """原图视野的像素宽（列方向上有视野像素的跨度）。"""
    import numpy as np
    f = rgb_or_fov if rgb_or_fov.dtype == bool else fov_mask(rgb_or_fov)
    cols = np.nonzero(f.any(axis=0))[0]
    return int(cols[-1] - cols[0] + 1) if cols.size else f.shape[1]


def scale_for_mag(mag, width0, w1x=W1X):
    """放大 mag 倍时的缩放比例（width0 = 原图宽）。"""
    return float(mag) * w1x / float(width0)


def mag_of_scale(s, width0, w1x=W1X):
    return s * float(width0) / w1x


def mag_cap(width0, cap, w1x=W1X):
    """这张图能"重新推理"的最大倍数：模型上限 cap（HRF 训练集的 Dice 曲线）和原图分辨率（层不能比原图大——
    再大就是把原图插值放大后送进网络，没有新的真实像素）取小。"""
    return min(float(cap), float(width0) / w1x)


def effective_mag(mag, width0, cap, w1x=W1X):
    """选的倍数 -> (推理倍数, 显示放大倍数)。超过 mag_cap：按上限推理，其余只插值放大显示。"""
    c = mag_cap(width0, cap, w1x)
    return (float(mag), 1.0) if mag <= c + 1e-9 else (c, float(mag) / c)


def level_size(w0, h0, s):
    """缩放后的尺寸（四舍五入）和补到 8 的倍数后的层尺寸。"""
    lw, lh = max(8, int(round(w0 * s))), max(8, int(round(h0 * s)))
    return (lw, lh), (ceil_to(lw, SIZE_ALIGN), ceil_to(lh, SIZE_ALIGN))


def make_level(rgb, s):
    """原图 (h0, w0, 3) uint8 按 s 缩放（LANCZOS）+ 右 / 下补黑到 8 的倍数 -> (层图, (有效宽, 有效高))。"""
    import numpy as np
    from PIL import Image
    h0, w0 = rgb.shape[:2]
    (lw, lh), (W, H) = level_size(w0, h0, s)
    im = Image.fromarray(np.ascontiguousarray(rgb, np.uint8))
    if (lw, lh) != (w0, h0):
        im = im.resize((lw, lh), Image.LANCZOS)
    out = np.zeros((H, W, 3), np.uint8)
    out[:lh, :lw] = np.asarray(im)
    return out, (lw, lh)


def box_to_level(box0, s, level_w, level_h):
    """原图坐标的框 -> 层坐标（向外取整，夹在层内）。"""
    x0, y0, w0, h0 = box0
    x = max(0, int(math.floor(x0 * s)))
    y = max(0, int(math.floor(y0 * s)))
    x1 = min(level_w, int(math.ceil((x0 + w0) * s)))
    y1 = min(level_h, int(math.ceil((y0 + h0) * s)))
    return x, y, max(1, x1 - x), max(1, y1 - y)


def box_to_orig(box, s):
    x, y, w, h = box
    return x / s, y / s, w / s, h / s


# ---------------------------------------------------------------------------------------------
# 按内容建议放大区域（总览的 logit 上算）
# ---------------------------------------------------------------------------------------------
def tile_scores(overview_logits, fov, thr, band, grid):
    """总览 logit 切成 grid x grid 像素的格子 -> 每格 (不确定像素比, 血管像素比, 视野像素比)。
    不确定 = |logit - thr| <= band（与 flags b5 置信度同一定义）。只数视野内。"""
    import numpy as np
    H, W = overview_logits.shape
    q = overview_logits.astype(np.int16)
    unsure = (np.abs(q - thr) <= band) & fov
    vessel = (q >= thr) & fov
    gy, gx = -(-H // grid), -(-W // grid)
    out = np.zeros((gy, gx, 3))
    for j in range(gy):
        for i in range(gx):
            sl = (slice(j * grid, (j + 1) * grid), slice(i * grid, (i + 1) * grid))
            n = fov[sl].sum()
            if n:
                out[j, i] = (unsure[sl].sum() / n, vessel[sl].sum() / n, n / fov[sl].size)
    return out


def suggest(overview_logits, fov, thr, band, grid=96, k=5, min_fov=0.5):
    """建议精查的区域：完整的、视野占比 >= min_fov 的格子按不确定像素比从高到低取 k 个（总览层坐标的框）。"""
    sc = tile_scores(overview_logits, fov, thr, band, grid)
    H, W = overview_logits.shape
    cand = [(sc[j, i, 0], (i * grid, j * grid, grid, grid))
            for j in range(sc.shape[0]) for i in range(sc.shape[1])
            if sc[j, i, 2] >= min_fov and (i + 1) * grid <= W and (j + 1) * grid <= H]
    cand.sort(key=lambda c: -c[0])
    H, W = overview_logits.shape
    return [(u, (x, y, min(grid, W - x), min(grid, H - y))) for u, (x, y, _, _) in cand[:k]]


def order_by_uncertainty(tiles, level_w, level_h, overview_logits, overview_fov, thr, band):
    """块的计算顺序：把每块自有区映射回总览层，按不确定像素比从高到低（跳过的块排最后）。返回块下标列表。"""
    import numpy as np
    oh, ow = overview_logits.shape
    fy, fx = oh / float(level_h), ow / float(level_w)
    q = overview_logits.astype(np.int16)
    unsure = (np.abs(q - thr) <= band) & overview_fov
    for t in tiles:
        y0, y1 = int(t.oy * fy), max(int(t.oy * fy) + 1, int(math.ceil((t.oy + t.oh) * fy)))
        x0, x1 = int(t.ox * fx), max(int(t.ox * fx) + 1, int(math.ceil((t.ox + t.ow) * fx)))
        n = overview_fov[y0:y1, x0:x1].sum()
        t.score = float(unsure[y0:y1, x0:x1].sum()) / n if n else -1.0
    return sorted(range(len(tiles)), key=lambda i: (tiles[i].skip, -tiles[i].score, i))


# ---------------------------------------------------------------------------------------------
# 板端病例任务 JOB_TILE（服务端 v5.20 起，src/job_tile.c）
# ---------------------------------------------------------------------------------------------
JOB_TILE = 8
TILE_LOGITS, TILE_MASK, TILE_OVERLAY, TILE_NO_SKIP, TILE_PS_POST = 1, 2, 4, 8, 16
TILE_HDR = 72
LEVEL_MAGIC = 0x4C444C50          # "PLDL"
LEVEL_HDR = 32
UNSURE_RGB = (255, 200, 0)        # 不确定带的颜色（= jobs.overlay 的默认值）


def level_file_bytes(level_rgb):
    """板上 /data 里的层文件：32 字节头（"PLDL"、版本 1、宽、高、3）+ RGB 行。"""
    import struct
    import numpy as np
    H, W = level_rgb.shape[:2]
    return struct.pack("<5I12x", LEVEL_MAGIC, 1, W, H, 3) + np.ascontiguousarray(level_rgb, np.uint8).tobytes()


def union_rect(tiles):
    x0 = min(t.tx for t in tiles)
    y0 = min(t.ty for t in tiles)
    x1 = max(t.tx + t.tw for t in tiles)
    y1 = max(t.ty + t.th for t in tiles)
    return x0, y0, x1 - x0, y1 - y0


def tile_request(level_w, level_h, box, model=0, thr=-3, band=0, region=None, path=None, order=None):
    """JOB_TILE 的参数。region = 上传的层区域 (x, y, w, h)（path 为 None 时）；path = 板上层文件（/data/...）。"""
    import struct
    order = list(order or [])
    pb = path.encode("utf-8") if path else b""
    rx, ry, rw, rh = region if region is not None else (0, 0, 0, 0)
    p = struct.pack("<II ii II iiii iiii II", 1 if path else 0, model, thr, band, level_w, level_h,
                    *[int(v) for v in box], rx, ry, rw, rh, len(order), len(pb))
    return p + struct.pack("<%dH" % len(order), *order) + pb


def mask_row_bytes(ow16):
    return (ow16 + 127) // 128 * 16


def parse_tile_blob(d, flags):
    """"tile" 块 -> dict（头字段 + logits (oh, ow) int8 / mask (oh, ow) bool / overlay (oh, ow, 3) uint8）。"""
    import struct
    import numpy as np
    keys = ("index", "pos", "tx", "ty", "tw", "th", "ox", "oy", "ow", "oh", "state", "run_us", "crop_us",
            "post_us", "ovl_us", "vessels", "comps", "reserved")
    v = dict(zip(keys, struct.unpack_from("<2I8i8I", d, 0)))
    v["skip"] = bool(v["state"] & 1)
    ow, oh, off = v["ow"], v["oh"], TILE_HDR
    if not v["skip"]:
        if flags & TILE_LOGITS:
            v["logits"] = np.frombuffer(d, np.int8, ow * oh, off).reshape(oh, ow)
            off += ow * oh
        if flags & TILE_MASK:
            mrow = mask_row_bytes(ceil_to(ow, 16))
            bits = np.frombuffer(d, np.uint8, oh * mrow, off).reshape(oh, mrow)
            v["mask"] = np.unpackbits(bits, axis=1, bitorder="little")[:, :ow].astype(bool)
            off += oh * mrow
        if flags & TILE_OVERLAY:
            v["overlay"] = np.frombuffer(d, np.uint8, ow * oh * 3, off).reshape(oh, ow, 3)
            off += ow * oh * 3
    if off != len(d):
        raise ValueError("tile blob: %d bytes, expected %d" % (len(d), off))
    return v


def run_board(link, level_w, level_h, box, flags=TILE_LOGITS, level_rgb=None, path=None, order=None, model=0,
              thr=-3, band=0, on_tile=None, on_progress=None):
    """板上分块推理一个框。像素来源：path（板上 /data 的层文件，板子只裁剪）或 level_rgb（上传需要的区域）。
    返回 dict(result, tiles=[解析后的块], logits / mask / overlay = 拼好的框（按 flags；跳过的块 = -128 / False / 黑），
    upload_s, wall_s)。
    on_tile(块 dict) 每收到一块调一次（逐块填热图）。"""
    import numpy as np
    import jobs
    tiles = plan(box, level_w, level_h)
    region = None
    data = b""
    if path is None:
        region = union_rect(tiles)
        rx, ry, rw, rh = region
        data = np.ascontiguousarray(level_rgb[ry:ry + rh, rx:rx + rw], np.uint8).tobytes()
    params = tile_request(level_w, level_h, box, model, thr, band, region, path, order)
    got = []
    link.close()          # 板端会断开空闲的连接（看图、框选可能很久）：每个任务新连一次，几毫秒

    def blob(name, d):
        if name != "tile":
            return False
        t = parse_tile_blob(d, flags)
        got.append(t)
        if on_tile is not None:
            on_tile(t)
        return True
    r = jobs.run(link, JOB_TILE, flags, params, data, on_progress, on_blob=blob)
    res = r["result"]
    out = dict(result=res, tiles=sorted(got, key=lambda t: t["index"]), upload_s=r["upload_s"], wall_s=r["wall_s"],
               plan=tiles)
    if not res.get("ok"):
        return out
    x, y, w, h = box
    for key, flag, fill, shape in (("logits", TILE_LOGITS, OUTSIDE_LOGIT, (h, w)), ("mask", TILE_MASK, False, (h, w)),
                                   ("overlay", TILE_OVERLAY, 0, (h, w, 3))):
        if not flags & flag:
            continue
        a = np.full(shape, fill, dict(logits=np.int8, mask=bool, overlay=np.uint8)[key])
        for t in got:
            if key in t:
                a[t["oy"] - y:t["oy"] - y + t["oh"], t["ox"] - x:t["ox"] - x + t["ow"]] = t[key]
        out[key] = a
    return out


def overlay_ref(base_rgb, logits, thr, band=0):
    """板上叠加（vis_layer 或 PS，alpha 16）的参照：血管（logit >= thr）涂红，|logit - thr| <= band 涂不确定色。
    = ai_view_overlay_rgb（band = 0）/ visref.overlay。"""
    import numpy as np
    out = np.array(base_rgb, np.uint8, copy=True)
    q = logits.astype(np.int16)
    out[q >= thr] = (255, 0, 0)
    if band > 0:
        out[np.abs(q - thr) <= band] = UNSURE_RGB
    return out
