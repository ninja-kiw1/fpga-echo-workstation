# -*- coding: utf-8 -*-
"""jobs.py —— 病例任务（控制头 v7 "HM"，板端 src/job.c）：上传一次，整个病例在板上跑完。

和紧凑帧（v6c）的区别：紧凑帧是"PC 发一帧、板子算一帧"，编码、后处理、流程控制都在 PC；
病例任务是"PC 发一个序列（CT 文件、两帧图……），板上做完编码 → PL 推理 → 后处理 → 决策"，只回数值和掩膜。

请求：24 字节头（u16 0x4D48、u8 任务、u8 版本 1、u32 flags、u32 参数字节、u32 数据字节、u32 参数+数据的 CRC-32、u32 0）
      + 参数 + 数据。
回复：一串消息，每条 16 字节头（u16 0x524A、u8 类型、u8 0、u32 长度、u32 CRC-32、u32 序号）+ 负载：
      1 进度（JSON）、2 数据块（u8 名字长度、名字、u8 编码 0 原样 / 1 zlib、u32 原长、数据）、3 结果（JSON）、4 结束（u32 状态）。
板端在任务运行期间至少每 0.5 s 发一条进度，所以 PC 的套接字超时（15 s）不会误触发。

纯 Python（struct / zlib / json），不依赖 numpy；numpy 视图由调用方自己做。
"""
import json
import os
import struct
import time
import zlib

JOB_MAGIC = 0x4D48            # "HM"
MSG_MAGIC = 0x524A            # "JR"
JOB_VERSION = 1
JOB_CAPS, JOB_CT_SEG, JOB_REG_SEARCH, JOB_ECHO_EF, JOB_SIMPSON_EF, JOB_RENDER3D, JOB_OVERLAY = 0, 1, 2, 3, 4, 5, 6
MSG_PROGRESS, MSG_BLOB, MSG_RESULT, MSG_END = 1, 2, 3, 4

# JOB_CT_SEG flags（src/job_ct.c）。放大默认在 PL 的 pp_layer 上做（v5.14 起；之前默认 PS）；CT_PS_UPSAMPLE 改用 PS 工作线程
CT_LOGITS, CT_MASK_COARSE, CT_MASK_FINAL, CT_PS_UPSAMPLE, CT_INPUTS, CT_PL_UPSAMPLE = 1, 2, 4, 8, 16, 32
# v5.16：板上三维表面（surf3d.c，ct_volume.Surface 的 C 移植），回 "render3d" 图块 + 结果 "surface"；
# CT_SLICES 回窗宽窗位后的放射科视图各层 "slices_u8"（板上界面显示用）
CT_RENDER3D, CT_SLICES = 64, 128
# JOB_REG_SEARCH flags（src/job_reg.c）
REG_COSTS = 1
# JOB_ECHO_EF flags（src/job_echo.c）：LIVE = 每个分析帧回一个 "frame" 块 + 滚动 EF；PACE = 按视频自身帧率送帧（模拟探头）
# 视频只在 PC 上解码（OpenCV），上传灰度帧；板上解码（libjpeg）和 OpenCV 差几个灰度级，10-13 决定不做
# 最大块 + 填洞默认在 PL 的 pp_layer 上做（v5.14 起）；ECHO_PS_CLEAN 改用 PS 工作线程（v5.12/5.13 板端 bit2 的意思相反）
ECHO_LOGITS, ECHO_MASKS, ECHO_PS_CLEAN, ECHO_LIVE, ECHO_PACE = 1, 2, 4, 16, 32
# 部分 E 心肌应变（src/job_strain.c，服务端 v5.19 起）：清理默认在 PL 的 pp_layer；STRAIN_PS_CLEAN 改用 PS；
# STRAIN_C_KERNEL 追踪用纯 C 的 SAD 核（只为对照计时，结果相同）；STRAIN_LOGITS 多回两张 128 logit（板上核对用）
JOB_STRAIN = 7
STRAIN_PS_CLEAN, STRAIN_C_KERNEL, STRAIN_LOGITS = 1, 2, 4


class JobError(Exception):
    pass


def pack_request(kind, flags, params, data):
    body = bytes(params) + bytes(data)
    head = struct.pack("<HBBIIIII", JOB_MAGIC, kind, JOB_VERSION, flags, len(params), len(data),
                       zlib.crc32(body) & 0xFFFFFFFF, 0)
    return head, body


def _blob(payload):
    n = payload[0]
    name = payload[1:1 + n].decode("utf-8", "replace")
    enc = payload[1 + n]
    raw_len = struct.unpack_from("<I", payload, 2 + n)[0]
    data = payload[6 + n:]
    if enc == 1:
        data = zlib.decompress(data)
    if len(data) != raw_len:
        raise JobError("blob %s: %d bytes, expected %d" % (name, len(data), raw_len))
    return name, data


def run(link, kind, flags=0, params=b"", data=b"", on_progress=None, chunk=1 << 20, on_blob=None):
    """发一个任务并收完全部回复 -> dict(result, blobs, progress, status, upload_s, wall_s)。
    link 是 imagelink.ImageLink（用它的 _send_all / _recv_exact，连接断了会自动重连）。
    on_progress(dict) 在收到每条进度时调用（上传期间也会以 stage="upload" 调用）。
    on_blob(name, bytes) 返回 True 时这个块交给调用方处理、不存进 blobs（边跑边回的逐帧结果用它）。"""
    head, body = pack_request(kind, flags, params, data)
    t0 = time.perf_counter()
    link._send_all(head)
    for i in range(0, len(body), chunk):                 # 分块发：sendall 的超时是整次调用的总时长
        link._send_all(body[i:i + chunk])
        if on_progress is not None and len(body) > chunk:
            on_progress(dict(stage="upload", done=min(i + chunk, len(body)), total=len(body)))
    t_up = time.perf_counter() - t0
    out = dict(result=None, blobs={}, progress=[], status=None, upload_s=t_up)
    seq = 0
    while True:
        h = link._recv_exact(16)
        magic, typ, _, n, crc, s = struct.unpack("<HBBIII", h)
        if magic != MSG_MAGIC:
            raise JobError("bad reply magic 0x%04X (board server too old for case jobs?)" % magic)
        if s != seq:
            raise JobError("message %d out of order (expected %d)" % (s, seq))
        seq += 1
        p = link._recv_exact(n) if n else b""
        if zlib.crc32(p) & 0xFFFFFFFF != crc:
            raise JobError("message %d CRC mismatch" % s)
        if typ == MSG_PROGRESS:
            pr = json.loads(p.decode("utf-8"))
            out["progress"].append(pr)
            if on_progress is not None:
                on_progress(pr)
        elif typ == MSG_BLOB:
            name, d = _blob(p)
            if on_blob is None or not on_blob(name, d):
                out["blobs"][name] = d
        elif typ == MSG_RESULT:
            out["result"] = json.loads(p.decode("utf-8"))
        elif typ == MSG_END:
            out["status"] = struct.unpack("<I", p)[0]
            break
        else:
            raise JobError("unknown message type %d" % typ)
    out["wall_s"] = time.perf_counter() - t0
    if out["result"] is None:
        raise JobError("job ended without a result")
    return out


def caps(link):
    """板端支持哪些任务；老板端（v5.10 及以前）不认 "HM" 头，会直接断开 -> LinkError。"""
    return run(link, JOB_CAPS)["result"]


def ct_params(coarse=14, fine=15, level=40, width=400, target_mm=5):
    return struct.pack("<BBhHBB", coarse, 255 if fine is None else fine, level, width, target_mm, 0)


def ct_seg(link, path, coarse=14, fine=15, flags=CT_MASK_FINAL, on_progress=None, data=None, views=0, size=0):
    """整卷 CT 上传给板子，板上做完两阶段分割（fine=None 只做粗分割）。data 可直接给文件内容。
    flags 带 CT_RENDER3D 时（v5.16+）板上还渲染三维表面：views 个角度（0 = 每 30° 一张共 12 张）、size 边长（0 = 320）。"""
    if data is None:
        with open(path, "rb") as f:
            data = f.read()
    params = ct_params(coarse, fine) + (struct.pack("<BBH", views, 0, size) if flags & CT_RENDER3D else b"")
    return run(link, JOB_CT_SEG, flags, params, data, on_progress)


def unpack_render3d(r):
    """ct_seg / render3d 的结果 -> [每个角度一张 (size, size, 3) uint8]（老板端没有就是 []）。"""
    import numpy as np
    s = (r.get("result") or {}).get("surface")
    b = r["blobs"].get("render3d")
    if not s or b is None or "size" not in s:
        return []
    n = s["size"]
    a = np.frombuffer(b, np.uint8).reshape(-1, n, n, 3)
    return [a[i] for i in range(len(a))]


def render3d(link, mask, spacing, angles=(), size=320, step=2):
    """板上三维表面渲染（v5.16 JOB_RENDER3D）：mask [z,512,512] bool + 间距 (dx,dy,dz) mm -> 每个角度一张图。"""
    import numpy as np
    m = np.ascontiguousarray(mask, bool)
    params = struct.pack("<HHBBH3d", m.shape[0], size, len(angles), step, 0, *[float(v) for v in spacing])
    params += struct.pack("<%dd" % len(angles), *[float(a) for a in angles])
    r = run(link, JOB_RENDER3D, 0, params, np.packbits(m.ravel(), bitorder="little").tobytes())
    return unpack_render3d(r), r["result"]


# JOB_OVERLAY（v5.16）：PL（vis_layer）合成叠加图，没有 vis_layer 的板子在 PS 上按同一规格算（"engine" 说明在哪算的）
OVL_BASE_NONE, OVL_BASE_GREY, OVL_BASE_RGB, OVL_BASE_HEAT = 0, 1, 2, 3
OVL_HAS_LOGIT, OVL_HAS_BITS, OVL_FILL_FROM_BITS, OVL_FILL, OVL_UNSURE, OVL_CONTOUR = 8, 16, 32, 64, 128, 256


def overlay(link, ow, oh, flags, base=None, logits=None, bits=None, lut=None, thr=0, band=0, fill=(255, 0, 0), fill_a=16,
            unsure=(255, 200, 0), unsure_a=16, contour=(0, 255, 0), force_ps=False):
    """-> ((oh, ow, 3) uint8, result dict)。alpha 以 1/16 计（16 = 直接涂色，与 ai_view_overlay_rgb 相同）；
    logits [lh, lw] int8（最近邻放大）；bits [mh, mw] bool；lut [256, 3]（热力图，下标 logit + 128）。"""
    import numpy as np
    lh, lw = np.shape(logits) if logits is not None else (0, 0)
    mh, mw = np.shape(bits) if bits is not None else (0, 0)
    rgb = lambda c: c[0] | c[1] << 8 | c[2] << 16
    params = struct.pack("<16i", ow, oh, flags, lw, lh, mw, mh, thr, band, fill_a, unsure_a, rgb(fill), rgb(unsure),
                         rgb(contour), 1 if force_ps else 0, 0)
    data = b""
    if flags & 3 in (OVL_BASE_GREY, OVL_BASE_RGB):
        data += np.ascontiguousarray(base, np.uint8).tobytes()
    if flags & OVL_HAS_LOGIT:
        data += np.ascontiguousarray(logits, np.int8).tobytes()
    if flags & OVL_HAS_BITS:
        b = np.zeros((mh, (mw + 127) // 128 * 128), bool)
        b[:, :mw] = bits
        data += np.packbits(b, axis=1, bitorder="little").tobytes()
    if flags & 3 == OVL_BASE_HEAT:
        data += np.ascontiguousarray(lut, np.uint8).tobytes()
    r = run(link, JOB_OVERLAY, 0, params, data)
    return np.frombuffer(r["blobs"]["overlay"], np.uint8).reshape(oh, ow, 3), r["result"]


def unpack_mask(bits, shape):
    """位压缩掩膜（第 k 个体素 = 字节 k>>3 的第 k&7 位）-> numpy bool 数组。"""
    import numpy as np
    n = 1
    for s in shape:
        n *= s
    return np.unpackbits(np.frombuffer(bits, np.uint8), bitorder="little")[:n].reshape(shape).astype(bool)


def reg_params(mode=0, radius=7, model=5, steps=(4, 2, 1), engine=0, subpel=0, size=128):
    st = list(steps)[:4] + [0] * (4 - min(4, len(steps)))
    p = struct.pack("<BBBB4B", mode, radius, model, engine, *st)
    if subpel or size != 128:
        p += struct.pack("<BB", subpel, 1 if size == 256 else 0)
    return p


def _costs(tab):
    return {(struct.unpack_from("<b", tab, i)[0], struct.unpack_from("<b", tab, i + 1)[0]):
            struct.unpack_from("<d", tab, i + 2)[0] for i in range(0, len(tab), 10)}


def reg_search(link, a128, b128, mode=0, radius=7, model=5, steps=(4, 2, 1), costs=True, engine=0, subpel=0, size=128):
    """F5 配准搜索整个在板上做：A、B 两帧 128x128 灰度进，(dx, dy) 出。
    mode 0 = 三步搜索（steps），1 = 全搜索（±radius）。costs=True 时带回每个算过的候选的代价。
    engine=1（v5.16+，r2 固件）：平移、插值、代价和搜索全在 PL 的 vis_layer 上，整数结果与 engine=0 逐位相同；
    板子没有 vis_layer 时板端自动走老路，结果里 "engine" 写明实际走的哪条（老板端没有这个字段 = U-Net 老路）。
    subpel（1 = 半像素，2 = 1/4 像素）和 size=256 只有 PL 引擎支持；结果另有 "qx"/"qy"（1/4 像素）和 "costs_q"。"""
    r = run(link, JOB_REG_SEARCH, REG_COSTS if costs else 0, reg_params(mode, radius, model, steps, engine, subpel, size),
            bytes(a128) + bytes(b128))
    res = r["result"]
    res.setdefault("engine", "unet")
    if costs and "costs" in r["blobs"]:
        res["costs"] = _costs(r["blobs"]["costs"])
    if costs and "costs_q" in r["blobs"]:
        res["costs_q"] = _costs(r["blobs"]["costs_q"])
    res["wall_s"] = r["wall_s"]
    return res


def echo_params(conf, scale, model=16, realtime=False, nframes=0, fps=0.0, board_fps=25.0):
    """echo_ef 的 128 字节参数。conf = echo_ws.load_config()（逐拍分析参数、两套校准、质控门限来自训练集；
    最后 24 字节是临床测量的门限（复核边界宽度、RR 变异门限、多拍平均的拍数），在验证集上定。v5.14 及以前的板端只读前 104 字节）。"""
    import echo_ws
    c = conf["cfg"]
    thr = conf.get("qc_thresholds", (0.35, 0.6))
    cc = echo_ws.clinical_conf(conf, "realtime" if realtime else "full")
    return struct.pack("<6BHd14d", model, 0, 1 if realtime else 0, int(c["smooth"]),
                       1 if c["agg"] == "mean" else 0, 0, nframes, float(fps), c["dist_s"], c["prom"], c["win"],
                       conf["full"]["a"], conf["full"]["b"], conf["realtime"]["a"], conf["realtime"]["b"],
                       float(scale), float(thr[0]), float(thr[1]), float(board_fps),
                       float(cc["review_margin"]), float(cc["rr_cv_irregular"]), float(cc["min_beats_avg"]))


KF_KIND = {"full": 0, "hold": 1, "fixed": 2, "adaptive": 3}


def load_kf_config(mode="keyframe"):
    """echo_kf_config.json（E:/fpga/my_project/pld2026final/03_算法与数据/quality/make_kf_config.py 生成）里的一套：关键帧调度 + 跟踪参数 + 这套调度的 EF 校准
    + 质量灯（②）+ 保形区间（③）的全部数字。mode = "keyframe"（推荐档）或 "full"（每帧推理，同样出 ②③）。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_kf_config.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)[mode]


def echo_kf_params(base, kf):
    """服务端 v5.21 起 echo_ef mode 2（src/job_echo.c job_echo_kf）：128 字节基本参数（mode 字节改成 2）+ 560 字节扩展
    "KF01"（布局见 job_echo.c）。kf = load_kf_config() 的一套。"""
    b = bytearray(base)
    b[2] = 2
    p, t = kf["policy"], kf["track"]
    win = p.get("win")
    ext = struct.pack("<I11i4d", 0x3130464B, KF_KIND[p["kind"]], int(p.get("n", 0)), int(p.get("n_max", 0)),
                      int(p.get("n_min", 1)), int(win) if win is not None and win >= 0 else -1, int(t["R"]), int(t["k"]),
                      int(t["reg"]), int(t.get("pad", 24)), 1, 0,
                      float(p["res"]) if p.get("res") else 0.0, float(t["spacing"]), float(kf["a"]), float(kf["b"]))
    m = kf["model"]
    vals = list(m["w"]) + list(m["mu"]) + list(m["sd"]) + [m["t_green"], m["t_red"]] + list(m["b_sigma"]) + \
        list(m["zmu"]) + list(m["zsd"]) + [m["q80"], m["q90"]]
    assert len(vals) == 60
    ext += struct.pack("<60d", *[float(v) for v in vals])
    assert len(ext) == 560
    return bytes(b) + ext


def load_heart_config():
    """echo_heart_config.json（E:/fpga/my_project/pld2026final/03_算法与数据/wallmotion/make_heart_config.py 生成）：节段室壁运动（HMC-QU DEV 拟合）+ 右心程序号 /
    合理性门限。没有文件时回 None。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_heart_config.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


HEART_WM, HEART_RH = 1, 2


def heart_block(hc, flags=HEART_WM | HEART_RH):
    """服务端 v5.24 起 mode 2 / 3 可选的 176 字节 "HC01"（布局见 src/cardiac_ext.h）：接在 KF01（mode 2）或 LV01（mode 3）后面。"""
    w = hc["wall_motion"]
    r = hc["right_heart"]
    b = struct.pack("<IBBBB", 0x31304348, int(flags), int(r["rv_model"]), int(r["ra_model"]), 0)
    b += struct.pack("<6d6dd4ddddd", *[float(v) for v in w["mu"]], *[float(v) for v in w["sd"]], float(w["t_seg"]),
                     *[float(v) for v in w["w"]], float(w["t_rec"]), float(r["min_frac"]), float(r["max_overlap"]), 0.0)
    assert len(b) == 176
    return b


def unpack_rh(d, pairs):
    """"rh_masks" 块 -> [(RV 舒张末, RV 收缩末, RA 收缩末) bool [112,112] 掩膜] * pairs。"""
    import numpy as np
    m = np.unpackbits(np.frombuffer(d, np.uint8), bitorder="little").reshape(-1, 112, 112).astype(bool)
    return [tuple(m[3 * k:3 * k + 3]) for k in range(pairs)]


def unpack_frame(d):
    """"frame" 块 -> dict(t, vol, ncomp, area, border, unc, mask=112*112/8 字节的位掩膜)。"""
    t, vol, ncomp, area, border, unc = struct.unpack_from("<Idiiid", d, 0)
    return dict(t=t, vol=vol, ncomp=ncomp, area=area, border=bool(border), unc=unc, mask=d[32:32 + 112 * 112 // 8])


def echo_ef(link, conf, scale, frames, fps, model=16, realtime=False, flags=ECHO_MASKS, on_progress=None,
            on_frame=None, keyframe=None, heart=None):
    """整段心超在板上分析：frames = PC 解码的 [T,112,112] uint8（numpy 或 bytes），fps = 帧率。
    on_frame(dict) 收到每个分析帧的结果（要 flags 带 ECHO_LIVE）。回 jobs.run 的结果。
    keyframe = load_kf_config() 的一套时走 mode 2（v5.21：关键帧调度 + 质量灯 + 保形 EF 区间，结果多 "keyframe" /
    "key" / "quality" / "interval"；每帧都分析，realtime 不起作用；mode 2 不发 "frame" 块）。
    heart = load_heart_config()（只配 keyframe 用，板端 v5.24+）：结果多 "wall_motion" / "right_heart"，blobs 多 "rh_masks"。"""
    data = bytes(frames) if not hasattr(frames, "tobytes") else frames.tobytes()
    params = echo_params(conf, scale, model, realtime, len(data) // (112 * 112), fps)
    if keyframe is not None:
        params = echo_kf_params(params, keyframe)
        if heart is not None:
            params += heart_block(heart)

    def blob(name, d):
        if name == "frame" and on_frame is not None:
            on_frame(unpack_frame(d))
            return True
        return False
    return run(link, JOB_ECHO_EF, flags, params, data, on_progress, on_blob=blob)


LIVE_FRAMES112, LIVE_TRACE, LIVE_FRAME = 1, 2, 4   # mode 3 输出：结束时回 112 帧、每帧时间戳；v5.23 起每个 live 块带本帧
LIVE_STAMPS = ("captured", "ready", "start", "work0", "work1", "end", "sent_prev")


def echo_live_params(base, kf, name, source="file", crop=None, max_frames=4096, detect_frames=50, thr=8, every=1,
                     out_flags=LIVE_FRAMES112 | LIVE_TRACE, v4l2=(640, 480, 60.0)):
    """服务端 v5.22 起 echo_ef mode 3（src/job_echo.c job_echo_live）：mode 2 的 688 字节 + 112 字节 "LV01"。
    source "file"：name = 板上 /root/echo_src 里的 .y8；"v4l2"：name = "video0" 等，v4l2 = 想要的 (W, H, fps)。
    crop = (x, y, 边长) 固定裁切，None = 前 detect_frames 帧自动找运动方块（门限 thr）。every = 每几帧回一个 "live" 块。"""
    b = bytearray(echo_kf_params(base, kf))
    b[2] = 3
    cx, cy, cs = crop if crop else (0, 0, 0)
    nm = name.encode("ascii")
    assert len(nm) < 64
    lv = struct.pack("<IBBHHHHH3iHHdII64s", 0x3130564C, {"file": 0, "v4l2": 1}[source], 1 if crop else 0,
                     int(max_frames), int(detect_frames), int(thr), int(every), 0, int(cx), int(cy), int(cs),
                     int(v4l2[0]), int(v4l2[1]), float(v4l2[2]), int(out_flags), 0, nm)
    assert len(lv) == 112
    return bytes(b) + lv


ROLL = dict(win=50, every=10, view_every=25, view_n=3, view_t=0.27)   # v5.25：灯每 10 帧（50 fps 下 0.2 s）更新；每 5 帧时板上 DP 界面同时在跑，P99 到 83 ms


def roll_block(win=50, every=5, view_every=25, view_n=3, rv_model=0, view_t=0.27, chain=0, f1_every=0, f1_model=0,
               camus=None):
    """服务端 v5.25 起 mode 3 可选的 32 字节 "RQ01"（布局见 src/job_echo.c）：接在 LV01（有 HC01 时接在 HC01）后面。
    滚动质量灯：每 every 帧算最近 win 帧；切面：每 view_every 帧跑一次右室程序（rv_model 0 = HC01 的），
    最近 view_n 次 右室 / 左室 面积比的中位数 >= view_t 判四腔（门限在 CAMUS TRAIN 上定，E:/PLD/quality/scan_sim）。
    v5.26：chain=1 走 CAMUS 口径（caps "live_roll_camus"；只用于 CAMUS 这类画面的源，见 ROLL_CAMUS）：每 f1_every 帧在 PL
    上跑一次 F1 左室程序（f1_model），后面接 248 字节 "CQ01" = camus 模型（w[10] / mu[9] / sd[9] / t_green / t_red）；
    camus["hold_thr"] > 0（灰度级）= 判定后保持：灯定了就停 F1 / 右室程序，1 s 平均画面变化超过门限（探头动了）再重判。"""
    b = struct.pack("<IHHHHidIHH", 0x31305152, int(win), int(every), int(view_every), int(view_n), int(rv_model),
                    float(view_t), int(chain), int(f1_every), int(f1_model))
    assert len(b) == 32
    if chain in (1, 2):           # 2 = 1 + F1 关键帧引擎（11-02）
        m = camus
        assert len(m["w"]) == 10 and len(m["mu"]) == 9 and len(m["sd"]) == 9
        c = struct.pack("<II10d9d9ddd", 0x31305143, int(round(1000 * float(m.get("hold_thr", 0.0)))), *map(float, m["w"]), *map(float, m["mu"]), *map(float, m["sd"]),
                        float(m["t_green"]), float(m["t_red"]))
        assert len(c) == 248
        b += c
    return b


def load_roll_camus(path=None):
    """v5.26 CAMUS 口径的滚动质量灯 + 切面（E:/PLD/quality/live_camus/fit_dll.py 用板上同一份 C 在 CAMUS TRAIN 上定的
    标准化，权重 / 门限 = quality_camus.json portable；VAL 区分 Poor AUC 见文件 "val"）。返回 echo_live(roll=...) 用的 dict。
    只给 CAMUS 这类画面的源（live/src/index.json 里 "quality": "camus"）：F1 分不好 EchoNet 画面（Dice 0.47）。
    11-02："engine": "f1" -> chain 2，现场引擎的关键帧也用 F1，EF 用 ef_a / ef_b 校准（CAMUS TRAIN，quality/live_camus/f1_engine.py），
    发送时要把 KF01 的 a / b 换成这两个（heartview 里做）。"""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "camus_live_model.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    return dict(win=m["win"], every=m["every"], view_every=m["view_every"], view_n=m["view_n"], rv_model=m["rv_model"],
                view_t=m["view_t"], chain=2 if m.get("engine") == "f1" else 1, f1_every=m["f1_every"],
                f1_model=m["f1_model"], camus=m)   # chain 2：关键帧也用 F1（caps "live_f1_engine"）


def unpack_live(d):
    """"live" 块 -> dict(t, key, vol, stamps={名字: 任务开始后的 µs}, mask=112*112/8 字节的位掩膜,
    frame=112*112 字节的本帧或 None（要 LIVE_FRAME，板端 v5.23+）,
    roll_light=滚动质量灯 0/1/2 或 None（还没出 / 没开 RQ01）, view=2 四腔 / 1 非四腔 / 0 还没判断, roll_score,
    hold=True：v5.26 CAMUS 口径已判定、探头未动，暂停判断）。"""
    t, key, rl, vw, hold, vol = struct.unpack_from("<IBBBBd", d, 0)
    st = struct.unpack_from("<7I", d, 16)
    score = struct.unpack_from("<f", d, 44)[0]
    m0 = 48 + 112 * 112 // 8
    return dict(t=t, key=bool(key), vol=vol, stamps=dict(zip(LIVE_STAMPS, st)), mask=d[48:m0],
                frame=d[m0:m0 + 112 * 112] if len(d) >= m0 + 112 * 112 else None,
                roll_light=rl - 1 if rl else None, view=vw, roll_score=score, hold=bool(hold))


def echo_live(link, conf, scale, kf, name, source="file", crop=None, model=16, on_live=None, on_progress=None, heart=None,
              roll=None, **kw):
    """现场模式（服务端 v5.22 echo_ef mode 3）：帧来自板上的信号源（.y8 文件按自身帧率回放 / 采集卡），PC 不上传帧。
    板上：前 detect_frames 帧找运动方块 -> 只留方块、整数盒滤波缩到 112 -> mode 2 同一套引擎逐帧跑。
    on_live(dict) 收每个 "live" 块（unpack_live）；进度里 stage="live" 带滚动 EF（已校准）/ 心率 / 延迟。
    结果 = mode 2 的全部字段 + "stream"（源、裁切、帧数、延迟）；blobs 可含 "frames112"、"trace"（每帧 7 个 u32 时间戳）。
    heart = load_heart_config()（板端 v5.24+）：同 echo_ef，多 "wall_motion" / "right_heart" / "rh_masks"。
    roll = dict（如 ROLL，板端 v5.25+ caps "live_roll"）：每个 live 块带滚动质量灯 / 切面，结果多 "rolling"。"""
    params = echo_live_params(echo_params(conf, scale, model, False, 0, 50.0), kf, name, source, crop, **kw)
    if heart is not None:
        params += heart_block(heart)
    if roll is not None:
        params += roll_block(**roll)

    def blob(n, d):
        if n == "live" and on_live is not None:
            on_live(unpack_live(d))
            return True
        return False
    return run(link, JOB_ECHO_EF, ECHO_MASKS, params, b"", on_progress, on_blob=blob)


def simpson_ef(link, masks, spacing_2ch, spacing_4ch, out_sizes=None, clean=False, on_progress=None, patient=None):
    """F4 双平面 Simpson 在板上算（板端 src/job_simpson.c，与 ef_simpson.volumes 逐位一致）。
    masks = 4 个 bool 掩膜，顺序 2CH ED, 2CH ES, 4CH ED, 4CH ES；spacing = (高, 宽) mm；
    out_sizes = 4 个 (w, h)：板上先按 Pillow NEAREST 缩放到这个尺寸（界面里 512 掩膜回原图尺寸），None = 不缩放；
    clean = 板上先做最大连通块 + 填洞（预测掩膜要，专家掩膜不要）。
    v5.16（P1 1.6）：masks / out_sizes 可以是 8 个，后 4 个依次 2CH ED 心外膜、4CH ED 心外膜、2CH ES 左房、4CH ES 左房，
    结果多 "cardiac"（左房截断 / 容积 / 校准、心外膜 / 心内膜容积、左室质量、壁厚；与 cardiac_measure.measure 逐位一致）。
    部分 A（下一版服务端）：cardiac 再多 lvidd_mm / rwt；patient = (身高 cm, 体重 kg, 性别 'F' / 'M' / None) 时
    再多 bsa_m2 / lvmi / lvmi_class（LVMI 增高与否，cardiac_measure.lvmi_class；RWT 只给数值）。旧服务端忽略多出的参数。"""
    params, data = simpson_request(masks, spacing_2ch, spacing_4ch, out_sizes, clean, patient)
    return run(link, JOB_SIMPSON_EF, 0, params, data, on_progress)


def simpson_request(masks, spacing_2ch, spacing_4ch, out_sizes=None, clean=False, patient=None):
    import numpy as np
    data = bytearray()
    for k, m in enumerate(masks):
        m = np.asarray(m, bool)
        h, w = m.shape
        ow, oh = out_sizes[k] if out_sizes else (w, h)
        data += struct.pack("<4HB3x", w, h, ow, oh, 1 if clean else 0)
        data += np.packbits(m.ravel(), bitorder="little").tobytes()
    params = struct.pack("<4d", spacing_2ch[0], spacing_2ch[1], spacing_4ch[0], spacing_4ch[1])
    if patient is not None and patient[0] and patient[1]:      # f64 身高 cm, f64 体重 kg, u8 性别 (0 / 1 F / 2 M), 7x
        params += struct.pack("<2dB7x", float(patient[0]), float(patient[1]), {"F": 1, "M": 2}.get(patient[2], 0))
    return params, bytes(data)


def strain_params(sx, sy, lv_model=2, epi_model=18):
    """f64 sx, f64 sy（512 网格上每像素 mm：原图宽 × 像素间距 x / 512，高同理），u32 左室模型、u32 心外膜模型、8 字节 0"""
    return struct.pack("<2d4I", float(sx), float(sy), lv_model, epi_model, 0, 0)


def strain(link, frames512, sx, sy, flags=0, on_progress=None, lv_model=2, epi_model=18):
    """部分 E 心肌应变（探索性辅助测量，src/job_strain.c，服务端 v5.19 起）。
    frames512：T 帧 512x512 u8（上位机 512 帧的 R 通道），第 0 帧 = ED、最后一帧 = ES（CAMUS 半周期序列）。
    板上：ED 帧 [::4, ::4] 在 PL 上跑模型 2（F1 左室）和程序 18（心外膜）→ PL 清理 → PS 提心肌中线、逐帧块匹配追踪
    → GLS-like 曲线（"gls_pct"）、ES 值（原始 / TRAIN 偏移后）。blobs："points"（i32 T x n x 2，(y, x) Q8）、
    "line"（i32 中线像素 (y, x)）、"myo"（512 x 512 位图）。老服务端回 "unknown job"。"""
    data = b"".join(bytes(f) for f in frames512)
    return run(link, JOB_STRAIN, flags, strain_params(sx, sy, lv_model, epi_model), data, on_progress)
