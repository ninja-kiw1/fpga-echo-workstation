# -*- coding: utf-8 -*-
"""imageio.py —— 图像的来源与显示转换。

提供：
  * 生成测试图样（gradient / bars / checker）
  * 读 **samples/** 目录里随工具分发的**默认测试图**（STARE 眼底图 im0001~im0005）；
    往目录里再丢图片、重启工具就会出现在下拉框里，不用改代码
  * **原生读** Netpbm 全家桶（P1..P7，不需要 Pillow）与裸 RGB888；
    其它格式（PNG/JPG/BMP/TIF/WebP…）走 Pillow
  * **原生写** 裸 RGB888 / PPM / PGM / PBM / PAM（不需要 Pillow）；
    其它格式走 Pillow
  * 转成 PPM，喂给 tkinter 的 PhotoImage 显示

统一的内部表示就是**裸 RGB888 的 bytes**，长度必须 786432。
取图的统一入口是 :func:`load_pattern`（名字，内置图样或 samples 都走它）
和 :func:`load_image`（文件路径）。

支持的图片格式一览（"原生" = 只靠标准库就能读写）::

    格式         读            写      说明
    ----------  -----------  ------  --------------------------------------
    裸 RGB888    ✅ 原生       ✅ 原生  .rgb/.raw/.bin，必须正好 786432 字节
    PPM  P1..P6  ✅ 原生       ✅ P6    ASCII/二进制都认，maxval 1..65535
    PGM  P2/P5   ✅ 原生       ✅ P5    灰度，导出用与 PL 相同的 (77R+150G+29B)>>8
    PBM  P1/P4   ✅ 原生       ✅ P4    1=黑；读时按位打包解析
    PNM  P1..P6  ✅ 原生       ✅ P6    扩展名 .pnm 一律按 P6 写
    PAM  P7      ✅ 原生       ✅ P7    depth 1/2/3/4，多出来的 alpha 丢掉
    PNG/JPG      ✅ Pillow     ✅ Pillow
    BMP/TIF/GIF  ✅ Pillow     ✅ Pillow
    WebP         ✅ Pillow     ✅ Pillow

非 512x512 的一律拉伸到 512x512（优先 Pillow LANCZOS；没装 Pillow 时对
Netpbm 回退最近邻）。**已经 512x512 的不重采样**，保证逐字节可比。
"""

import base64
import os

from proto import (IMG_TOTAL_BYTES, IMG_WIDTH, IMG_HEIGHT, IMG_BYTES_PER_PIX,
                   CTRL_THR_DEFAULT)

# 标准 8 色条，取 75% 幅度
_BAR_COLORS_75 = [
    (191, 191, 191), (191, 191, 0), (0, 191, 191), (0, 191, 0),
    (191, 0, 191), (191, 0, 0), (0, 0, 191), (0, 0, 0),
]


def make_gradient():
    """水平渐变(R 随 x) + 垂直渐变(G 随 y) + 固定蓝。"""
    buf = bytearray(IMG_TOTAL_BYTES)
    last = IMG_WIDTH - 1
    for y in range(IMG_HEIGHT):
        base = y * IMG_WIDTH * IMG_BYTES_PER_PIX
        g = (y * 255) // (IMG_HEIGHT - 1)
        for x in range(IMG_WIDTH):
            p = base + x * IMG_BYTES_PER_PIX
            buf[p] = (x * 255) // last
            buf[p + 1] = g
            buf[p + 2] = 128
    return bytes(buf)


def make_colorbars():
    """8 条竖向彩条，每条 64 像素。"""
    buf = bytearray(IMG_TOTAL_BYTES)
    bar_w = IMG_WIDTH // len(_BAR_COLORS_75)
    for y in range(IMG_HEIGHT):
        base = y * IMG_WIDTH * IMG_BYTES_PER_PIX
        for x in range(IMG_WIDTH):
            r, g, b = _BAR_COLORS_75[min(x // bar_w, len(_BAR_COLORS_75) - 1)]
            p = base + x * IMG_BYTES_PER_PIX
            buf[p] = r
            buf[p + 1] = g
            buf[p + 2] = b
    return bytes(buf)


def make_checker():
    """32x32 像素黑白棋盘。"""
    buf = bytearray(IMG_TOTAL_BYTES)
    cell = 32
    for y in range(IMG_HEIGHT):
        base = y * IMG_WIDTH * IMG_BYTES_PER_PIX
        ycell = y // cell
        for x in range(IMG_WIDTH):
            v = 255 if (((x // cell) + ycell) & 1) else 0
            p = base + x * IMG_BYTES_PER_PIX
            buf[p] = v
            buf[p + 1] = v
            buf[p + 2] = v
    return bytes(buf)


PATTERNS = {
    "gradient": make_gradient,
    "bars": make_colorbars,
    "checker": make_checker,
}


# --------------------------------------------------------------------------
# samples/ —— 随工具一起分发的**默认测试图**
#
# 出厂的"图样"下拉框 = 三个内置图样(gradient/bars/checker) + samples/ 里所有图片。
# 想再加图：把文件丢进 samples/，重启工具即可，**不用改代码**。
#
# 目前带的是 STARE 眼底图 `im0001`~`im0005`（P6 700x605 maxval=255，
# 1.27 MB/张，**保留原始分辨率未缩放**）。选它们时按本工具的统一约定
# 拉伸到 512x512（Pillow LANCZOS）再发 —— 和"载入图片…"完全同一条路，
# 所以 CLI 上 `imagelink.py <ip> <port> im0003` 也能直接用。
# --------------------------------------------------------------------------
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")

# 扫描时认得的扩展名；同名不同扩展名（im0001.ppm 与 im0001.png）时按此顺序取第一个
SAMPLE_EXTS = (".ppm", ".pnm", ".pgm", ".pbm", ".pam",
               ".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".gif", ".webp",
               ".rgb", ".raw", ".bin")


def list_samples():
    """扫描 samples/ 目录，返回 ``{显示名: 绝对路径}``（按显示名排序）。

    显示名 = 文件名去掉扩展名（``im0001.ppm`` → ``im0001``）。

    目录不存在、或里面没有认得出的图片，一律返回**空 dict**而不是报错 ——
    用户把图删光了，工具靠三个内置图样照样能跑。
    """
    found = {}
    try:
        names = os.listdir(SAMPLE_DIR)
    except OSError:
        return found
    for fn in sorted(names):
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if not stem or ext not in SAMPLE_EXTS:
            continue
        path = os.path.join(SAMPLE_DIR, fn)
        if not os.path.isfile(path):
            continue
        prev = found.get(stem)
        if prev is None or SAMPLE_EXTS.index(ext) < SAMPLE_EXTS.index(
                os.path.splitext(prev)[1].lower()):
            found[stem] = path
    return found


# 导入时扫一次；运行中往 samples/ 里加图，重启工具就能看到。
SAMPLES = list_samples()


def figure_names():
    """下拉框里该列的全部名字（三个内置图样 + samples/ 里的图），已排序。

    重名时**内置图样优先** —— ``PATTERNS`` 里的名字是文档承诺过的，
    不能被 samples/ 里一个同名文件顶掉；这种 sample 只在 :func:`list_samples`
    里可见，且会被 :func:`load_pattern` 忽略。
    """
    return sorted(set(PATTERNS) | set(SAMPLES))


def load_pattern(name):
    """按名字取一张图，返回 ``(裸 RGB888 字节, 中文说明)``。

    名字先当 samples/ 里的图找，再回落到三个内置图样；两处都没有抛 ``KeyError``。
    出图规则与 :func:`load_image` 完全一致（非 512x512 拉伸，正好 512x512
    的不重采样），所以选出来的就是**真正会被发出去的那 786432 字节**。
    """
    path = SAMPLES.get(name)
    if path is not None and name not in PATTERNS:
        return load_image(path)
    try:
        fn = PATTERNS[name]
    except KeyError:
        raise KeyError("既不认识图样 %r，samples/ 里也没有它（可选：%s）"
                       % (name, figure_names())) from None
    return fn(), "内置图样 '%s'，%d 字节 RGB888" % (name, IMG_TOTAL_BYTES)


# --------------------------------------------------------------------------
# 通用：把任意尺寸的 RGB 弄成 512x512
# --------------------------------------------------------------------------
def _resize_nearest(rgb, w, h, tw, th):
    """纯 Python 最近邻缩放（没有 Pillow 时的兜底）。

    rgb 是 w*h*3 的裸 RGB888。返回 tw*th*3。
    """
    out = bytearray(tw * th * 3)
    for ty in range(th):
        sy = (ty * h) // th
        srow = sy * w * 3
        trow = ty * tw * 3
        for tx in range(tw):
            sx = (tx * w) // tw
            s = srow + sx * 3
            t = trow + tx * 3
            out[t] = rgb[s]
            out[t + 1] = rgb[s + 1]
            out[t + 2] = rgb[s + 2]
    return bytes(out)


def _to_target(rgb, w, h, srcnote, size=(IMG_WIDTH, IMG_HEIGHT)):
    """已到目标尺寸就原样返回（逐字节不变）；size=None 时不做缩放（原生尺寸）。"""
    if size is None:
        return rgb, "%s（原生尺寸，未缩放）" % srcnote
    if (w, h) == size:
        return rgb, ("%s → 尺寸已是 %dx%d，未重采样（逐字节不变）"
                     % (srcnote, size[0], size[1]))
    try:
        from PIL import Image
    except ImportError:
        return (_resize_nearest(rgb, w, h, size[0], size[1]),
                "%s → 已拉伸为 %dx%d（最近邻；本机没装 Pillow）"
                % (srcnote, size[0], size[1]))
    im = Image.frombytes("RGB", (w, h), rgb).resize(size, Image.LANCZOS)
    return im.tobytes(), "%s → 已拉伸为 %dx%d（Pillow LANCZOS）" % (srcnote, size[0], size[1])


# --------------------------------------------------------------------------
# Netpbm 家族（P1..P7）—— 原生解码/编码，**不依赖 Pillow**
#
#   P1 PBM  ASCII 位图       P4 PBM  二进制位图
#   P2 PGM  ASCII 灰度       P5 PGM  二进制灰度
#   P3 PPM  ASCII 彩色       P6 PPM  二进制彩色
#   P7 PAM  带表头的任意通道（灰度/灰度+alpha/RGB/RGBA）
#
# 全部支持: 头里任意位置 # 注释、maxval 1..65535（>255 每样本 2 字节大端）、
# 以及 PBM 的 1=黑/0=白 语义。FPGA/图像处理圈子里这几种是最常见的交换格式。
# --------------------------------------------------------------------------
NETPBM_EXTS = (".ppm", ".pgm", ".pbm", ".pnm", ".pam")
_PBM_ASCII, _PGM_ASCII, _PPM_ASCII = b"P1", b"P2", b"P3"
_PBM_RAW, _PGM_RAW, _PPM_RAW, _PAM_RAW = b"P4", b"P5", b"P6", b"P7"


def _is_netpbm(data):
    return data[:1] == b"P" and data[1:2] in (b"1", b"2", b"3", b"4", b"5", b"6", b"7")


class _PnmReader(object):
    """解析 netpbm 头 + 光栅。"""

    def __init__(self, data, path="<data>"):
        self.d = data
        self.path = path
        self.i = 0

    def _skip_ws_and_comments(self):
        d, n = self.d, len(self.d)
        while self.i < n:
            c = d[self.i]
            if c == 0x23:                       # '#'
                while self.i < n and d[self.i] not in (0x0A, 0x0D):
                    self.i += 1
            elif c in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
                self.i += 1
            else:
                return
        raise ValueError("%s: unexpected end of header" % self.path)

    def token(self):
        self._skip_ws_and_comments()
        start = self.i
        d, n = self.d, len(self.d)
        while self.i < n and d[self.i] not in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
            self.i += 1
        if self.i == start:
            raise ValueError("%s: empty token" % self.path)
        return d[start:self.i]

    def int_token(self):
        return int(self.token())

    def end_header(self):
        """光栅起点：**只吃掉一个空白字符**（后面可能就是 0x0A/0x20 这样的像素值）。

        典型的 netpbm 陷阱：这里若"跳过所有空白"，以 0x0A 或 0x20 开头的像素
        就会被吞掉，整幅图随之错位。
        """
        d = self.d
        if self.i < len(d) and d[self.i] in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
            if d[self.i] == 0x0D and self.i + 1 < len(d) and d[self.i + 1] == 0x0A:
                self.i += 2                     # CRLF 算一个分隔
            else:
                self.i += 1
        return self.i

    def ascii_samples(self, count, maxval):
        out = []
        for _ in range(count):
            v = self.int_token()
            if v < 0 or v > maxval:
                raise ValueError("%s: sample %d out of range 0..%d"
                                 % (self.path, v, maxval))
            out.append(v)
        return out

    def bin_samples(self, count, maxval):
        step = 1 if maxval < 256 else 2
        need = count * step
        chunk = self.d[self.i:self.i + need]
        if len(chunk) != need:
            raise ValueError("%s: raster truncated (need %d bytes, got %d)"
                             % (self.path, need, len(chunk)))
        self.i += need
        if step == 1:
            return list(chunk)
        return [(chunk[k] << 8) | chunk[k + 1] for k in range(0, need, 2)]


def load_netpbm(path, size=(IMG_WIDTH, IMG_HEIGHT)):
    """读 netpbm（P1..P7）→ (裸 RGB888, w, h, 说明)。不依赖任何第三方库。

    size=None 时按原生尺寸返回(不缩放)——自检/工具用; 默认拉伸到 512x512。
    """
    with open(path, "rb") as f:
        data = f.read()
    if not _is_netpbm(data):
        raise ValueError("%s: 不是 netpbm 文件（应以 P1..P7 开头）" % path)

    r = _PnmReader(data, path)
    magic = data[:2]
    r.i = 2

    if magic == _PAM_RAW:
        return _load_pam(r, size)

    w = r.int_token()
    h = r.int_token()
    if w <= 0 or h <= 0:
        raise ValueError("%s: bad size %dx%d" % (path, w, h))

    if magic == _PBM_ASCII:
        vals = r.ascii_samples(w * h, 1)
        return _finish_pbm(vals, w, h, path, size)
    if magic == _PBM_RAW:
        r.end_header()
        row_bytes = (w + 7) // 8
        need = row_bytes * h
        chunk = data[r.i:r.i + need]
        if len(chunk) != need:
            raise ValueError("%s: raster truncated (%d, need %d)" % (path, len(chunk), need))
        bits = []
        for y in range(h):
            base = y * row_bytes
            for x in range(w):
                byte = chunk[base + (x >> 3)]
                bits.append((byte >> (7 - (x & 7))) & 1)
        return _finish_pbm(bits, w, h, path, size)

    maxval = r.int_token()
    if maxval < 1 or maxval > 65535:
        raise ValueError("%s: bad maxval %d" % (path, maxval))

    if magic == _PGM_ASCII:
        g = r.ascii_samples(w * h, maxval)
        rgb = _gray_to_rgb(g, maxval)
    elif magic == _PPM_ASCII:
        s = r.ascii_samples(w * h * 3, maxval)
        rgb = _scale_rgb(s, maxval)
    elif magic == _PGM_RAW:
        r.end_header()
        g = r.bin_samples(w * h, maxval)
        rgb = _gray_to_rgb(g, maxval)
    elif magic == _PPM_RAW:
        r.end_header()
        s = r.bin_samples(w * h * 3, maxval)
        rgb = _scale_rgb(s, maxval)
    else:
        raise ValueError("%s: unsupported netpbm magic %r" % (path, magic))

    note = "Netpbm %s %dx%d maxval=%d" % (magic.decode(), w, h, maxval)
    return _to_target(rgb, w, h, note, size)


def _load_pam(r, size):
    """P7 (PAM): 行式表头 + 通用通道数。"""
    depth = None
    width = None
    height = None
    maxval = None
    tupletype = ""
    while True:
        tok = r.token()
        if tok == b"ENDHDR":
            break
        if tok == b"WIDTH":
            width = r.int_token()
        elif tok == b"HEIGHT":
            height = r.int_token()
        elif tok == b"DEPTH":
            depth = r.int_token()
        elif tok == b"MAXVAL":
            maxval = r.int_token()
        elif tok == b"TUPLTYPE":
            tupletype = r.token().decode(errors="replace")
        else:
            raise ValueError("%s: unexpected PAM header token %r" % (r.path, tok))
    if None in (depth, width, height, maxval):
        raise ValueError("%s: PAM header missing WIDTH/HEIGHT/DEPTH/MAXVAL" % r.path)
    if maxval < 1 or maxval > 65535:
        raise ValueError("%s: PAM bad maxval %d" % (r.path, maxval))
    if depth not in (1, 2, 3, 4):
        raise ValueError("%s: PAM DEPTH=%d unsupported" % (r.path, depth))
    r.i = r.end_header()
    s = r.bin_samples(width * height * depth, maxval)

    rgb = bytearray(width * height * 3)
    for p in range(width * height):
        base = p * depth
        if depth == 1:
            v = _scale8(s[base], maxval)
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
        elif depth == 2:                        # 灰度 + alpha：丢掉 alpha
            v = _scale8(s[base], maxval)
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
        else:                                   # RGB / RGBA：丢掉 alpha
            rgb[p * 3] = _scale8(s[base], maxval)
            rgb[p * 3 + 1] = _scale8(s[base + 1], maxval)
            rgb[p * 3 + 2] = _scale8(s[base + 2], maxval)
    note = "Netpbm P7 (PAM) %dx%d depth=%d maxval=%d%s" % (
        width, height, depth, maxval, (" %s" % tupletype) if tupletype else "")
    return _to_target(bytes(rgb), width, height, note, size)


def _scale8(v, maxval):
    """按 maxval 线性缩放到 0..255。"""
    if maxval == 255:
        return v
    return (v * 255 + maxval // 2) // maxval


def _scale_rgb(samples, maxval):
    if maxval == 255:
        return bytes(samples)
    out = bytearray(len(samples))
    for k, v in enumerate(samples):
        out[k] = _scale8(v, maxval)
    return bytes(out)


def _gray_to_rgb(gray, maxval):
    out = bytearray(len(gray) * 3)
    for k, v in enumerate(gray):
        v8 = _scale8(v, maxval)
        out[k * 3] = out[k * 3 + 1] = out[k * 3 + 2] = v8
    return bytes(out)


def _finish_pbm(bits, w, h, path, size):
    """PBM 每像素 1 bit：**1 = 黑(0), 0 = 白(255)**。"""
    out = bytearray(w * h * 3)
    for k, b in enumerate(bits):
        v = 0 if b else 255
        out[k * 3] = out[k * 3 + 1] = out[k * 3 + 2] = v
    return _to_target(bytes(out), w, h, "Netpbm PBM %dx%d (1=黑)" % (w, h), size)


# --------------------------------------------------------------------------
# 从文件读
# --------------------------------------------------------------------------
def load_raw(path):
    """读裸 RGB888 文件，长度必须正好 786432。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) != IMG_TOTAL_BYTES:
        raise ValueError("%s: %d bytes, expected %d"
                         % (path, len(data), IMG_TOTAL_BYTES))
    return data


def load_ppm(path):
    """读 P6 二进制 PPM（只支持 maxval<=255）。保留给老调用方；新代码用 load_netpbm。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"P6":
        raise ValueError("%s: not a P6 PPM" % path)
    idx, n, fields = 2, len(data), []
    while len(fields) < 3:
        while idx < n and data[idx:idx + 1].isspace():
            idx += 1
        if data[idx:idx + 1] == b"#":
            while idx < n and data[idx:idx + 1] not in (b"\n", b"\r"):
                idx += 1
            continue
        start = idx
        while idx < n and not data[idx:idx + 1].isspace():
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1
    w, h, maxv = fields
    if maxv > 255:
        raise ValueError("%s: maxval=%d not supported" % (path, maxv))
    if (w, h) != (IMG_WIDTH, IMG_HEIGHT):
        raise ValueError("%s: %dx%d, expected %dx%d"
                         % (path, w, h, IMG_WIDTH, IMG_HEIGHT))
    payload = data[idx:idx + IMG_TOTAL_BYTES]
    if len(payload) != IMG_TOTAL_BYTES:
        raise ValueError("%s: pixel data truncated (%d)" % (path, len(payload)))
    return payload


def load_and_stretch(path, size=(IMG_WIDTH, IMG_HEIGHT)):
    """用 Pillow 读**任意格式**图片 → 转 RGB → 拉伸到 512x512 → 裸 RGB888 字节。

    已经是目标尺寸的**不做重采样**，保证"载入一张 512x512 的图再发出去"逐字节不变
    —— 这点对验证很重要(回图要和原图 bit-exact 比对)。

    @return (raw_bytes, note)  note 是给界面显示的中文说明。
    """
    try:
        from PIL import Image
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError(
            "读这个格式需要 Pillow，但这台机器上没装上。\n"
            "装法：python -m pip install --user pillow\n"
            "（PPM/PGM/PBM/PNM/PAM 和裸 RGB888 不需要 Pillow，可以直接用）\n"
            "原始错误：%s" % e)

    with Image.open(path) as im:
        src_mode = im.mode
        w, h = im.size
        rgb = im if im.mode == "RGB" else im.convert("RGB")
        data = rgb.tobytes()

    return _to_target(data, w, h, "%dx%d %s" % (w, h, src_mode), size)


def load_image(path):
    """读入任意图片并**统一成 512x512 RGB888**，返回 (raw_bytes, note)。

    分发规则（按顺序）：

      * 文件头是 ``P1..P7``（Netpbm 家族）→ **原生解码**，不依赖 Pillow；
        支持 PBM/PGM/PPM/PAM 的 ASCII 与二进制两种写法、maxval 1..65535；
      * `.rgb` / `.raw` / `.bin` → 裸 RGB888，长度必须正好 786432，**原样透传**；
      * 其它（PNG / JPG / BMP / GIF / TIF / WebP …）→ 交给 Pillow 解码。

    非 512x512 的一律拉伸（优先 Pillow LANCZOS；没装 Pillow 时对 Netpbm
    回退到最近邻），**目标尺寸的不重采样**以保证逐字节可比。

    `note` 是一句中文说明，界面上直接显示。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".rgb", ".raw", ".bin"):
        data = load_raw(path)
        return data, "裸 RGB888，%d 字节，原样透传" % len(data)

    # Netpbm: 认文件头(比扩展名可靠), 但也接受扩展名提示
    with open(path, "rb") as f:
        head = f.read(2)
    if _is_netpbm(head):
        return load_netpbm(path)

    return load_and_stretch(path)


def save_raw(path, image):
    with open(path, "wb") as f:
        f.write(image)


def _luma_gray(image):
    """RGB888 → 8 位灰度，权重与 PL 侧 img_proc_axis 的 (77R+150G+29B)>>8 一致。"""
    out = bytearray(len(image) // 3)
    for k in range(0, len(image), 3):
        out[k // 3] = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
    return bytes(out)


def luma_histogram(image, exclude_extremes=False):
    """256 桶灰度直方图，灰度定义与 PL 完全一致((77R+150G+29B)>>8)。

    这样算出来的阈值直接就是"板子会用的那个灰度"上的阈值 —— 阈值分割之前
    板子算的正是这个灰度，所以预设值选得准。

    ``exclude_extremes=True`` 时**不计灰度为 0 或 255 的像素**：
    纯黑/纯白往往只是背景（图像的黑边、过曝白块、拼图留白），
    把它们算进去会把 Otsu 拽向"背景 vs 内容"而不是"内容内部的明暗分界"。
    """
    hist = [0] * 256
    n = len(image) - 2
    if exclude_extremes:
        for k in range(0, n, 3):
            v = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
            if v != 0 and v != 255:
                hist[v] += 1
        return hist
    for k in range(0, n, 3):
        hist[(77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8] += 1
    return hist


def otsu_threshold(hist):
    """Otsu 大津法：取让"前景/背景两类间方差"最大的那个门限，返回 0..255。

    纯 Python 遍历 256 个候选，与像素数无关，很快。直方图全空或全在一个桶上时
    返回 CTRL_THR_DEFAULT。
    """
    total = sum(hist)
    if total <= 0:
        return CTRL_THR_DEFAULT
    sum_all = 0
    for v in range(256):
        sum_all += v * hist[v]

    w_b = 0
    sum_b = 0
    best_t = CTRL_THR_DEFAULT
    best_var = -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = float(sum_b) / w_b
        m_f = float(sum_all - sum_b) / w_f
        var = float(w_b) * float(w_f) * (m_b - m_f) * (m_b - m_f)
        if var > best_var:
            best_var = var
            best_t = t
    return best_t


def _smooth_hist(hist, r=1):
    """滑动窗口取平均的直方图（用于找谷，避免被单桶噪声带偏）。

    半径只取 1：窗口再大就会把"只有一个桶宽的尖峰"抹平，谷的判定跟着失准
    （踩过：r=3 时合成图里 60% 的近黑峰被摊到 7 个桶上，谷反而不够深）。
    """
    out = [0.0] * 256
    for v in range(256):
        a = v - r if v - r > 0 else 0
        b = v + r + 1 if v + r + 1 < 256 else 256
        out[v] = float(sum(hist[a:b])) / float(b - a)
    return out


def _border_dark_fraction(image, gray_max, width=2):
    """图像外圈（默认 2 像素宽）里，灰度 < gray_max 的像素占比。

    用来分辨"背景"和"暗物体"：**背景总贴着图像边缘**（黑边、圆窗外的黑角），
    而画面中间的深色物体不是。只在边框上采样，代价可以忽略。
    """
    w = IMG_WIDTH
    h = IMG_HEIGHT
    dark = 0
    total = 0
    for y in range(h):
        edge_row = (y < width) or (y >= h - width)
        for x in range(w):
            if not edge_row and width <= x < w - width:
                continue                       # 只走外圈
            k = (y * w + x) * 3
            g = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
            total += 1
            if g < gray_max:
                dark += 1
    return float(dark) / float(total) if total else 0.0


def auto_gray_floor(image, hist=None, t1=None):
    """自动定"灰度最小值"：暗半区里**第一个符合条件的最深谷** = 暗背景的右沿。

    为什么要这么绕：暗端常常不止一个结构。以眼底图为例，从暗到亮是
    "一圈细黑边(0-3) → 谷(~8) → 深色背景环(16-31) → 深谷(~35) → 内容(48+)"。
    **取第一个谷会切在 8**，只削掉 3.7% 的细边、背景环原封不动；
    得让"左侧暗区至少占 5%"这条把 8 筛掉，才轮到 35（正好是背景环的右沿，削 26%）。

    候选谷要同时满足四条才采用，否则返回 0（=不排除任何像素）：
    ① 是局部极小；② 左侧暗区 ≥ 全图 5%；③ 谷深 ≤ 两侧峰较小者的 30%；
    ④ **暗区要贴在图像边框上**（≥30% 的边框像素是暗的）—— 背景总在边上，
    画面中间的深色物体不该被当背景排掉。平坦直方图（渐变图）在③被挡掉，
    细边（<5%）在②被挡掉，居中的暗物体在④被挡掉。

    @param image 裸 RGB888（④ 需要在像素上看边框）
    @param hist  可选，全图直方图
    @param t1    可选，全图一级 Otsu 阈值（只用来做"暗端有没有东西可切"的粗筛）
    @return 灰度下限 0..127；0 表示不排除
    """
    if hist is None:
        hist = luma_histogram(image)
    if t1 is None:
        t1 = otsu_threshold(hist)
    if t1 <= 1:
        return 0                        # 一级 Otsu 就是 0：暗端没有可切的东西
    total = sum(hist)
    if total <= 0:
        return 0

    sm = _smooth_hist(hist, 1)
    for v in range(1, 128):             # 背景只在暗半区
        if sm[v] > sm[v - 1] or sm[v] > sm[v + 1]:
            continue                    # 只考虑局部极小
        if sum(hist[:v]) * 20 < total:  # ① 左边的"暗背景"至少 5%
            continue
        peak_l = max(sm[:v + 1])
        peak_r = max(sm[v:])
        if peak_l <= 0.0 or peak_r <= 0.0:
            continue
        if sm[v] * 10.0 > 3.0 * min(peak_l, peak_r):     # ② 谷要够深（≤30%）
            continue
        if _border_dark_fraction(image, v) < 0.30:       # ③ 暗区得贴着边框
            continue
        return v                        # 第一个满足条件的谷
    return 0


def sobel_magnitude(image):
    """Sobel 幅度图（|gx|+|gy| 截到 255），返回 512×512 的 bytes。

    与 PL 的 mode 2 同构（同样的 3×3 权重、同样的边界零填充、同样的"约化梯度"），
    用于在 PC 上给"边缘阈值"挑一个推荐值。**不强求与 PL 逐值一致** ——
    板子上真正的边缘图以 PL 的输出为准。
    """
    gray = _luma_gray(image)
    w = IMG_WIDTH
    h = IMG_HEIGHT
    out = bytearray(w * h)
    for y in range(1, h - 1):
        r0 = (y - 1) * w
        r1 = y * w
        r2 = (y + 1) * w
        for x in range(1, w - 1):
            tl = gray[r0 + x - 1]
            tc = gray[r0 + x]
            tr = gray[r0 + x + 1]
            ml = gray[r1 + x - 1]
            mr = gray[r1 + x + 1]
            bl = gray[r2 + x - 1]
            bc = gray[r2 + x]
            br = gray[r2 + x + 1]
            gx = (tl + 2 * ml + bl) - (tr + 2 * mr + br)
            gy = (tl + 2 * tc + tr) - (bl + 2 * bc + br)
            mag = abs(gx) + abs(gy)
            out[r1 + x] = 255 if mag > 255 else mag
    return bytes(out)


def edge_threshold(image, pct=92, floor=24):
    """给"边缘叠加"(模式 6)挑一个推荐阈值：**Sobel 幅度的 pct 分位**。

    边缘幅度的直方图极度偏向 0（大多数像素是平坦区），Otsu 会被压得很低、
    画得满屏都是边。所以这里改用分位数："让大约 (100-pct)% 的像素被判成边缘"，
    视觉上干净可控。再取个下限 floor，避免平坦图也画出一堆噪点。

    @return (thr, note)
    """
    mag = sobel_magnitude(image)
    hist = [0] * 256
    for v in mag:
        hist[v] += 1
    total = sum(hist)
    if total <= 0:
        return CTRL_THR_DEFAULT, "空图"
    target = total * (100 - pct) // 100
    acc = 0
    thr = floor
    for v in range(255, -1, -1):
        acc += hist[v]
        if acc >= target:
            thr = max(v, floor)
            break
    note = ("边缘阈值(自动) = %d：Sobel 幅度前 %d%% 判为边缘、画白，其余保持原图"
            % (thr, 100 - pct))
    return thr, note


def auto_threshold(image, exclude_extremes=True, gray_min=None):
    """根据图片本身给一个合适的阈值分割门限（Otsu），返回 (thr, hist, note)。

    **算之前先自动定一个"灰度最小值"**（``gray_min``，None = 自动）：
    低于它的像素当作背景，**不计入**直方图（见 :func:`auto_gray_floor`）。
    另外灰度为 0/255 的纯黑/纯白像素也一律不计（图像黑边、过曝白块、拼图留白）。
    这些都只影响**挑阈值所用的统计** —— 板子仍然对**全部**像素应用这个阈值，
    所以提示里的占比是按全图算的，和板子回图对得上。
    排除得太狠（剩下不到 1/4）时会**自动退回全量**。

    再做**两级 Otsu**（很便宜，就是在同一张直方图上跑两次）：

    * 一级 = 背景/物体（最稳妥的默认值）。
    * 二级 = 把一级以下的所有桶清零后再跑一次 = "物体内部"
      （眼底图上正好是 **血管 vs 盘内背景**）。

    note 里两个值都写出来，界面上直接显示；用户也可以拖滑块自己试。
    注意 PL 的判据是 ``gray >= thr`` 为白，所以"黑"是 ``gray < thr``
    —— 统计时必须用 ``hist[:thr]``，别把 ``== thr`` 那个桶算进黑里。
    """
    hist_all = luma_histogram(image)
    total_all = sum(hist_all)

    floor = gray_min
    if floor is None:
        floor = auto_gray_floor(image, hist_all, otsu_threshold(hist_all))
    floor = max(0, min(int(floor), 255))

    hist = list(hist_all)
    # 低于灰度下限的一律当背景；下限为 0 时也仍然排掉纯黑(0)（用户 2026-10-02 的要求）
    lo_excl = max(floor, 1) if exclude_extremes else floor
    for v in range(0, min(lo_excl, 256)):
        hist[v] = 0
    if exclude_extremes:
        hist[255] = 0                                  # 过曝白块同理

    # 排除得太狠就退回全量（本来就是黑白的图，比如棋盘/二值图）
    excluded = total_all - sum(hist)
    if sum(hist) * 4 < total_all:
        hist = hist_all
        floor = 0
        excluded = 0

    thr = otsu_threshold(hist)
    # 占比按**全图**算：板子是把阈值应用到所有像素上的（含被排除的那些）
    black_all = sum(hist_all[:thr])
    note = "Otsu 一级=%d（全图黑占 %.0f%%）" % (
        thr, 100.0 * black_all / total_all if total_all else 0.0)
    if floor > 0 and total_all > 0:
        note += "｜排除< %d 的背景 %.0f%%" % (floor, 100.0 * excluded / total_all)

    # 二级：把一级以下清零后在同一张直方图上再跑一次 Otsu = "物体内部"的分割
    tail = list(hist)
    for v in range(0, min(thr + 1, 256)):
        tail[v] = 0
    tail_total = sum(tail)
    busy_bins = sum(1 for h in tail if h)
    # 物体内部要"够大"且"够分散"才值得报 —— 否则会给出误导性的值
    # (比如只有两个灰阶的合成图, 尾部只剩一个桶, Otsu 会吐出个 128)
    if tail_total > 0 and tail_total * 20 >= sum(hist) and busy_bins >= 8:
        thr2 = otsu_threshold(tail)
        if thr2 > thr:
            note += "｜二级=%d" % thr2
    return thr, hist, note


def to_pgm_bytes(image):
    """P5 二进制 PGM（灰度）。"""
    return b"P5\n%d %d\n255\n" % (IMG_WIDTH, IMG_HEIGHT) + _luma_gray(image)


def to_pbm_bytes(image, thr=128):
    """P4 二进制 PBM（1 = 黑）。按灰度阈值二值化。"""
    gray = _luma_gray(image)
    row_bytes = (IMG_WIDTH + 7) // 8
    out = bytearray(row_bytes * IMG_HEIGHT)
    for y in range(IMG_HEIGHT):
        base = y * row_bytes
        row = y * IMG_WIDTH
        for x in range(IMG_WIDTH):
            if gray[row + x] < thr:
                out[base + (x >> 3)] |= 0x80 >> (x & 7)
    return b"P4\n%d %d\n" % (IMG_WIDTH, IMG_HEIGHT) + bytes(out)


def to_pam_bytes(image):
    """P7 PAM（RGB, depth=3）。"""
    hdr = (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 3\nMAXVAL 255\n"
           b"TUPLTYPE RGB\nENDHDR\n" % (IMG_WIDTH, IMG_HEIGHT))
    return hdr + image


def save_image(path, image):
    """把裸 RGB888 存成文件, 格式由扩展名决定。

      * `.rgb` / `.raw` / `.bin` -> 裸 RGB888, 原样写;
      * `.ppm` / `.pnm`           -> P6 二进制 PPM;
      * `.pgm`                    -> P5 二进制 PGM（灰度, 与 PL 同一套权重）;
      * `.pbm`                    -> P4 二进制 PBM（阈值二值）;
      * `.pam`                    -> P7 PAM（RGB）;
      * 其它(.png/.jpg/.bmp/.tif/.webp) -> 交给 Pillow 编码(需要装 Pillow)。

    @param image  裸 RGB888 bytes, 长度必须 786432。
    @return 写入的路径(与入参相同)。
    """
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    ext = os.path.splitext(path)[1].lower()
    if ext in (".rgb", ".raw", ".bin"):
        save_raw(path, image)
    elif ext in (".ppm", ".pnm"):
        with open(path, "wb") as f:
            f.write(to_ppm_bytes(image))
    elif ext == ".pgm":
        with open(path, "wb") as f:
            f.write(to_pgm_bytes(image))
    elif ext == ".pbm":
        with open(path, "wb") as f:
            f.write(to_pbm_bytes(image))
    elif ext == ".pam":
        with open(path, "wb") as f:
            f.write(to_pam_bytes(image))
    else:
        try:
            from PIL import Image
        except ImportError as e:                    # pragma: no cover
            raise RuntimeError(
                "保存 %s 需要 Pillow, 但没装上。\n"
                "可以改用 .rgb / .ppm / .pgm / .pbm / .pam 扩展名存（这些不需要 Pillow）。\n"
                "原始错误：%s" % (ext, e))
        Image.frombytes("RGB", (IMG_WIDTH, IMG_HEIGHT), image).save(path)
    return path


# --------------------------------------------------------------------------
# 显示用：转 PPM
# --------------------------------------------------------------------------
def to_ppm_bytes(image):
    """把裸 RGB888 打包成 P6 PPM（tkinter 的 PhotoImage 认得）。"""
    return b"P6\n%d %d\n255\n" % (IMG_WIDTH, IMG_HEIGHT) + image


def make_photo(tk_module, image, keep=None):
    """给 tkinter 用的 PhotoImage。

    优先用 base64 内联数据（不落盘）；如果这个 Tk 构建不支持，再退回临时文件。
    `keep` 传一个 list 时，成功创建的 PhotoImage 会 append 进去，
    防止被 GC 掉（tkinter 的经典坑）。
    """
    ppm = to_ppm_bytes(image)
    photo = None
    try:
        photo = tk_module.PhotoImage(data=base64.b64encode(ppm))
    except Exception:
        photo = None

    if photo is None:
        tmp = os.path.join(os.environ.get("TEMP", "."), "_tcp_image_ui_%d.ppm"
                           % (abs(hash(image)) % 100000))
        with open(tmp, "wb") as f:
            f.write(ppm)
        photo = tk_module.PhotoImage(file=tmp)

    if keep is not None:
        keep.append(photo)
    return photo
