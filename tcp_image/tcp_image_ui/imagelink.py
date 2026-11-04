# -*- coding: utf-8 -*-
"""imagelink.py —— 与板子之间的 TCP 链路：发一帧、收一帧、量往返耗时。

计时口径（界面里显示的是"往返总耗时"）::

    t0 ──开始发送──► t1 ──发完──► 等板子处理并回传 ──► t2 ──收完整幅回图──►
    |<------ send_ms ------>|<------- reply_ms ------->|
    |<------------------ total_ms --------------------->|

命令行自测（不开界面就能对着板子验证收发与计时）::

    python imagelink.py 192.168.1.254 5060 bars
    python imagelink.py 192.168.1.254 5060 checker --repeat 5
    python imagelink.py 192.168.1.254 5060 bars --mode 3 --save out.png
"""

import os
import socket
import sys
import time

import imageio
import proto


class LinkError(Exception):
    """链路层错误（连接不上、对端关闭、超时、协议不符）。"""


class ImageLink(object):
    def __init__(self, host, port, timeout=15.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._sock = None
        self.pp_available = False     # 最近一帧紧凑回复说板端能做 PL 后处理（统计块 v8 + reserved bit2）

    # ---------------- 连接管理 ----------------
    @property
    def connected(self):
        return self._sock is not None

    def connect(self):
        self.close()
        self.pp_available = False     # 新连接可能是别的板端版本：先看到一帧 v8 回复再发 b10
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise LinkError("connect %s:%d failed: %s" % (self.host, self.port, e))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 把收发缓冲开大, 避免 786 KB 的帧被 TCP 窗口反压
        for opt, val in ((socket.SO_RCVBUF, 1 << 20), (socket.SO_SNDBUF, 1 << 20)):
            try:
                s.setsockopt(socket.SOL_SOCKET, opt, val)
            except OSError:
                pass
        s.settimeout(self.timeout)
        self._sock = s
        return s

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _ensure(self):
        if self._sock is None:
            self.connect()
        return self._sock

    # ---------------- 收发 ----------------
    def _send_all(self, data):
        s = self._ensure()
        try:
            s.sendall(data)
        except OSError as e:
            raise LinkError("send failed: %s" % e)

    def _recv_exact(self, n):
        s = self._ensure()
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                raise LinkError("timeout waiting for %d bytes (got %d)" % (n, len(buf)))
            except OSError as e:
                raise LinkError("recv failed: %s" % e)
            if not chunk:
                raise LinkError("peer closed after %d/%d bytes" % (len(buf), n))
            buf += chunk
        return bytes(buf)

    # ---------------- 紧凑帧（v6c，实时化）----------------
    def send_v6c(self, payload, w, **kw):
        """只发不收；与 recv_v6c 配对使用。连续发几帧再收 = 流水线（板端顺序处理，TCP 缓冲让收和算重叠）。"""
        self._send_all(proto.pack_v6c(payload, w, **kw))

    def recv_v6c(self, flags, w, nextra=0):
        """收一帧紧凑回复 -> (ai_stats 或 None, int8 logit bytes 或 None, 位掩膜 bytes 或 None)。
        nextra > 0（多程序流水线）时 logit 是列表：[程序 0, 额外程序 1, ...]。"""
        st = logit = mask = None
        n = w * w
        if flags & proto.FLAG_STATS:
            st = proto.unpack_stats(self._recv_exact(proto.AI_STAT_BYTES))
            if proto.expects_reg_block(st):
                st["reg"] = proto.unpack_reg(self._recv_exact(proto.REG_BLOCK_BYTES))
        if flags & proto.FLAG_C_LOGIT:
            outs = []
            for _ in range(1 + nextra):
                data = self._recv_exact(n + 2)
                if proto.crc16_ccitt_false(data[:n]) != data[n] | (data[n + 1] << 8):
                    raise LinkError("v6c logit CRC mismatch")
                outs.append(data[:n])
            logit = outs if nextra else outs[0]
        if flags & proto.FLAG_C_MASK:
            data = self._recv_exact(n // 8 + 2)
            if proto.crc16_ccitt_false(data[:n // 8]) != data[n // 8] | (data[n // 8 + 1] << 8):
                raise LinkError("v6c mask CRC mismatch")
            mask = data[:n // 8]
        if flags & proto.FLAG_C_PP:              # PL 后处理块排在最后，挂在统计块的 "pp" 上
            pp = proto.unpack_pp(self._recv_exact(proto.PP_BLOCK_BYTES))
            pp["mask"] = None
            if pp["mask_bytes"]:
                data = self._recv_exact(pp["mask_bytes"] + 2)
                nb = pp["mask_bytes"]
                if proto.crc16_ccitt_false(data[:nb]) != data[nb] | (data[nb + 1] << 8):
                    raise LinkError("v6c pp mask CRC mismatch")
                pp["mask"] = data[:nb]
            st = st if st is not None else {}
            st["pp"] = pp
        if st is not None and "version" in st:
            self.pp_available = proto.stats_pp_available(st)
        return st, logit, mask

    def v6c_round_trip(self, payload, w, **kw):
        """发一帧紧凑帧并等回复 -> (ai_stats, logit, mask, total_ms)。"""
        flags = kw.get("flags")
        if flags is None:
            flags = kw["flags"] = proto.FLAG_STATS | proto.FLAG_C_LOGIT
        t0 = time.perf_counter()
        try:
            self.send_v6c(payload, w, **kw)
            st, logit, mask = self.recv_v6c(flags, w, len(kw.get("extra", ())))
        except LinkError:
            self.close()
            raise
        return st, logit, mask, (time.perf_counter() - t0) * 1000.0

    def send_frame(self, image, mode=0, flags=proto.FLAG_DEFAULT, aiparam=None,
                   thr=proto.CTRL_THR_DEFAULT, version=4, roi=None):
        """按协议把一整幅图发出去。

        整帧先在 Python 里拼好（CRC 走 C 实现）再**一次性 sendall** ——
        发送系统调用从 512 次降到 1 次，发一帧从 ~610 ms 降到几毫秒。

        `mode`   处理模式 0..5（v2/v3/v4 头都带）。
        `flags`  回传内容位图，只在 v3/v4 头里（见 proto.FLAG_*）。
        `aiparam` AI 参数（输入源 + 分辨率 + 阈值），只在 v3/v4 头里；None = 默认。
        `thr`    阈值分割(mode 1)的门限 0..255，**只在 v4/v5 头里**。
        `roi`    (x, y, w, h)：ROI 局部推理，发 v5 头（板端统计块 version >= 3 才认）。
        """
        self._send_all(proto.pack_stream(image, mode, flags=flags, aiparam=aiparam,
                                         thr=thr, version=version, roi=roi))

    def recv_stream(self):
        """一次读满一整帧字节流（789504 B）。

        用 `recv_into` 填一个预分配的大缓冲，而不是每包读两次 ——
        接收系统调用从 ~1024 次降到个位数。
        """
        s = self._ensure()
        need = proto.STREAM_TOTAL_BYTES
        buf = bytearray(need)
        mv = memoryview(buf)
        got = 0
        try:
            while got < need:
                n = s.recv_into(mv[got:], need - got)
                if n == 0:
                    raise LinkError("peer closed after %d/%d bytes" % (got, need))
                got += n
        except socket.timeout:
            raise LinkError("timeout: got %d/%d bytes" % (got, need))
        except OSError as e:
            raise LinkError("recv failed: %s" % e)
        return bytes(buf)

    def recv_frame(self):
        """收一整幅回图，返回 (image_bytes, stats)。

        stats = {'rows_ok', 'crc_errors', 'seq_errors'}
        """
        return proto.unpack_stream(self.recv_stream())

    def recv_ai_stats(self):
        """收 64 字节 AI 统计块（flags 带 FLAG_STATS 时，在图像流之前）。"""
        return proto.unpack_stats(self._recv_exact(proto.AI_STAT_BYTES))

    def round_trip(self, image, mode=0, flags=proto.FLAG_DEFAULT, aiparam=None,
                   thr=proto.CTRL_THR_DEFAULT, version=4, roi=None):
        """发一帧、按 flags 收回相应内容。

        返回 ``(images, timings, rowstats, ai_stats)``：

        * ``images``   —— ``{'proc'|'overlay'|'mask'|'logit': bytes}``，按 flags 可能是空的；
          要 logit 时请同时带 FLAG_STATS，否则遇到老板端会一直等到超时；
        * ``timings``  —— ``{'send_ms', 'reply_ms', 'total_ms'}``；
        * ``rowstats`` —— 每个图像流自己的 ``{'rows_ok','crc_errors','seq_errors'}``；
        * ``ai_stats`` —— flags 带 FLAG_STATS 时的统计块 dict，否则 None。

        ``thr`` 是阈值分割(mode 1)的门限，只在 v4/v5 控制头里带。
        ``roi`` = (x, y, w, h) 时走 ROI 局部推理（v5 头）。
        连接**保持复用**：板端一个连接上连收多帧。出错才断开，下次自动重连。
        """
        if not self.connected:
            self.connect()
        try:
            t0 = time.perf_counter()
            self.send_frame(image, mode, flags=flags, aiparam=aiparam,
                            thr=thr, version=version, roi=roi)
            t1 = time.perf_counter()

            ai_stats = None
            if flags & proto.FLAG_STATS:
                ai_stats = self.recv_ai_stats()
                if proto.expects_morph_block(flags, ai_stats):   # 形态指标块紧跟统计块
                    ai_stats["morph"] = proto.unpack_morph(self._recv_exact(proto.MORPH_BYTES))
                if proto.expects_reg_block(ai_stats):            # 分块求和块（F5）在形态块之后
                    ai_stats["reg"] = proto.unpack_reg(self._recv_exact(proto.REG_BLOCK_BYTES))

            images, rowstats = {}, {}
            for name in proto.expected_images(flags, ai_stats):
                img, st = self.recv_frame()
                images[name] = img
                rowstats[name] = st
            t2 = time.perf_counter()
        except Exception:
            self.close()
            raise

        timings = {
            "send_ms": (t1 - t0) * 1000.0,
            "reply_ms": (t2 - t1) * 1000.0,
            "total_ms": (t2 - t0) * 1000.0,
        }
        return images, timings, rowstats, ai_stats

# --------------------------------------------------------------------------
# 命令行自测
# --------------------------------------------------------------------------
_FLAG_ALIASES = {
    "proc": proto.FLAG_ECHO_PROC,
    "mask": proto.FLAG_ECHO_MASK,
    "overlay": proto.FLAG_ECHO_OVER,
    "over": proto.FLAG_ECHO_OVER,
    "stats": proto.FLAG_STATS,
    "logit": proto.FLAG_ECHO_LOGIT,
    "morph": proto.FLAG_MORPH,
    "reg": proto.FLAG_REG,
    "noimage": proto.FLAG_NO_IMAGE,
    "none": proto.FLAG_NO_IMAGE,
}


def _parse_flags(text):
    """接受 'proc,mask,stats' / 'none' / '0x0007' / '7' 三种写法。"""
    text = text.strip()
    if text.lower().startswith("0x") or text.isdigit():
        return int(text, 0) & 0xFFFF
    flags = 0
    for part in text.replace("+", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in _FLAG_ALIASES:
            raise ValueError("unknown flag %r; use one of %s"
                             % (part, sorted(set(_FLAG_ALIASES))))
        flags |= _FLAG_ALIASES[part]
    return flags


def _cli(argv):
    host = argv[0] if len(argv) > 0 else "192.168.1.254"
    port = int(argv[1]) if len(argv) > 1 else 5060
    pattern = argv[2] if len(argv) > 2 else "bars"
    repeat = 1
    mode = 0
    flags = proto.FLAG_DEFAULT
    ai_src = proto.AIPARAM_SRC_RAW
    ai_thr = proto.AIPARAM_THR_DEFAULT
    ai_res = 512
    thr_spec = "auto"          # "auto" 或 0..255
    version = 4
    saves = {}
    if "--repeat" in argv:
        repeat = int(argv[argv.index("--repeat") + 1])
    if "--mode" in argv:
        mode = int(argv[argv.index("--mode") + 1])
    if "--flags" in argv:
        flags = _parse_flags(argv[argv.index("--flags") + 1])
    if flags & (proto.FLAG_ECHO_LOGIT | proto.FLAG_MORPH):
        flags |= proto.FLAG_STATS          # 靠统计块的 version 判断板端认不认 logit / 形态
    if "--ai-src" in argv:
        ai_src = (proto.AIPARAM_SRC_PROC if argv[argv.index("--ai-src") + 1] == "proc"
                  else proto.AIPARAM_SRC_RAW)
    model = None                           # --model fundus|skin|echo_lv|<下标>：模型库（板端 v5）
    if "--model" in argv:
        spec = argv[argv.index("--model") + 1]
        hit = [m for m in proto.MODELS if m["name"] == spec or str(m["index"]) == spec]
        if not hit:
            print("--model must be one of %s" % ", ".join(m["name"] for m in proto.MODELS))
            return 2
        model = hit[0]["index"]
        ai_thr = hit[0]["thr"]             # 该模型的默认门限；--ai-thr 可再覆盖
    if "--ai-thr" in argv:
        ai_thr = int(argv[argv.index("--ai-thr") + 1])
    if "--ai-res" in argv:
        ai_res = int(argv[argv.index("--ai-res") + 1])
        if proto.res_to_shift(ai_res) is None:
            print("--ai-res must be one of %s" % (proto.AI_RES_CHOICES,))
            return 2
    if "--thr" in argv:
        thr_spec = argv[argv.index("--thr") + 1].strip()
    roi = None
    if "--roi" in argv:                    # --roi x,y,w,h：ROI 局部推理（v5 头）
        roi = tuple(int(v) for v in argv[argv.index("--roi") + 1].split(","))
        flags |= proto.FLAG_STATS          # 统计块里有板端实际计算的裁剪尺寸
    if "--v2" in argv:
        version = 2
    if "--v3" in argv:
        version = 3
    if "--save" in argv:
        saves[""] = argv[argv.index("--save") + 1]
    for name in ("proc", "mask", "overlay"):
        key = "--save-" + name
        if key in argv:
            saves[name] = argv[argv.index(key) + 1]

    if not (0 <= mode <= proto.CTRL_MODE_MAX):
        print("mode must be 0..%d: %s"
              % (proto.CTRL_MODE_MAX,
                 " / ".join("%d %s" % (k, proto.MODE_NAMES[k])
                            for k in sorted(proto.MODE_NAMES))))
        return 2

    aiparam = proto.make_aiparam(ai_src, ai_thr, ai_res, model)
    print("proto self-check: %s" % ("OK" if not proto.SELF_CHECK_ERRORS
                                    else "FAILED %s" % proto.SELF_CHECK_ERRORS))
    print("模式: %d (%s)   flags=0x%04X %s   aiparam=0x%04X (src=%s thr=%d res=%s model=%s)   头=v%d"
          % (mode, proto.MODE_NAMES.get(mode, "?"), flags,
             proto.expected_images(flags), aiparam,
             "proc" if ai_src == proto.AIPARAM_SRC_PROC else "raw", ai_thr,
             "default" if ai_res == 512 and model is not None else ai_res,
             "board default" if model is None else proto.MODELS[model]["name"], version))

    # 第三个参数既可以是内置图样名 / samples/ 里的默认测试图名(im0001~im0005),
    # 也可以是一个图片文件路径
    # (PNG/JPG/BMP/TIF/WebP 走 Pillow; PPM/PGM/PBM/PNM/PAM 与裸 RGB888 原生读)
    if os.path.isfile(pattern):
        try:
            image, note = imageio.load_image(pattern)
        except Exception as e:
            print("读图失败: %s" % e)
            return 2
        print("图像: %s —— %s" % (pattern, note))
    elif pattern in imageio.PATTERNS or pattern in imageio.SAMPLES:
        try:
            image, note = imageio.load_pattern(pattern)
        except Exception as e:
            print("读图失败: %s" % e)
            return 2
        print("图像: %s —— %s" % (pattern, note))
    else:
        print("既不是文件也不是已知图样: %r (可选 %s)"
              % (pattern, imageio.figure_names()))
        return 2
    print("上线总长 %d 字节"
          % {4: proto.WIRE_TOTAL_BYTES_V4, 3: proto.WIRE_TOTAL_BYTES_V3,
             2: proto.WIRE_TOTAL_BYTES}[version])

    # ---- 阈值分割的门限: auto = 按这张图算 Otsu ----
    if thr_spec.lower() == "auto":
        thr, _hist, thr_note = imageio.auto_threshold(image)
        print("阈值分割: auto -> %s" % thr_note)
    else:
        try:
            thr = int(thr_spec, 0)
        except ValueError:
            print("--thr 只能是 auto 或 0..255，收到 %r" % thr_spec)
            return 2
        if not (0 <= thr <= 255):
            print("--thr 越界: %d (0..255)" % thr)
            return 2
        print("阈值分割: %d (手动指定)" % thr)

    link = ImageLink(host, port)
    total = []
    last = None
    for i in range(repeat):
        try:
            images, tim, rowstats, ai_stats = link.round_trip(
                image, mode, flags=flags, aiparam=aiparam, thr=thr, version=version, roi=roi)
        except LinkError as e:
            print("[%d] LINK ERROR: %s" % (i + 1, e))
            return 1
        total.append(tim["total_ms"])
        last = images
        parts = " ".join("%s(rows_ok=%d)" % (n, rowstats[n]["rows_ok"])
                         for n in images)
        print("[%d] total=%7.1f ms  (send %6.1f + reply %6.1f)  %s"
              % (i + 1, tim["total_ms"], tim["send_ms"], tim["reply_ms"],
                 parts if parts else "(no image)"))
        if ai_stats is not None:
            print("      AI: %ux%u thr=%d vessels=%u/%u (%.1f%%) comps=%u "
                  "logit[%d..%d] ai_us=%u gray[%u..%u] mode=%u frame=%u"
                  % (ai_stats["ai_res"], ai_stats["ai_res"], ai_thr,
                     ai_stats["vessels"], ai_stats["total"], ai_stats["vessels_pct"],
                     ai_stats["components"], ai_stats["logit_min"],
                     ai_stats["logit_max"], ai_stats["ai_us"],
                     ai_stats["proc_min"], ai_stats["proc_max"],
                     ai_stats["mode"], ai_stats["frame_count"]))

    link.close()
    if total:
        print("summary: n=%d  min=%.1f  max=%.1f  avg=%.1f ms"
              % (len(total), min(total), max(total), sum(total) / len(total)))
    if last:
        if "" in saves:
            first = last[sorted(last)[0]]
            imageio.save_image(saves[""], first)
            print("save: %s -> %s" % (sorted(last)[0], saves[""]))
        for name, path in saves.items():
            if name and name in last:
                imageio.save_image(path, last[name])
                print("save: %s -> %s" % (name, path))
            elif name:
                print("save: no %r image in the reply (flags=0x%04X)" % (name, flags))
    print("CLI_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
