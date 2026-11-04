# -*- coding: utf-8 -*-
"""heart_compat.py —— 板上（numpy 1.13.3 / scipy 0.19.1 / Python 3.6）跑心超工作站要补的两处 API 差异。

在 bootstrap.install() 之后、import 上位机代码之前调 install()；PC 上（新版本）什么都不做。
  1. np.packbits / np.unpackbits 的 bitorder="little"（numpy 1.17 才有）：jobs.unpack_mask、heartview 的逐帧掩膜都用它；
  2. scipy.signal.find_peaks（scipy 1.1 才有）：echo_ws.analyze（点击纠错后在上位机重算逐拍 EF）用它的 distance /
     prominence，这里按 scipy 的定义实现这两个条件（平顶峰取中点；distance 按峰高从高到低保留；prominence 无 wlen）。
"""

import sys

_done = [False]


def install():
    if _done[0]:
        return
    _done[0] = True
    _bits()
    _peaks()


def _bits():
    import numpy as np
    try:
        np.unpackbits(np.zeros(1, np.uint8), bitorder="little")
        return
    except TypeError:
        pass
    ou, op = np.unpackbits, np.packbits

    def unpackbits(a, axis=None, count=None, bitorder="big"):
        a = np.asarray(a, np.uint8)
        if axis is not None and axis not in (-1, a.ndim - 1):
            raise NotImplementedError("heart_compat.unpackbits: only the last axis")
        r = ou(a.ravel()) if axis is None else ou(a, axis=-1)
        if bitorder == "little":
            r = r.reshape(r.shape[:-1] + (-1, 8))[..., ::-1].reshape(r.shape)
        if count is not None:
            r = r[..., :count]
        return r

    def packbits(a, axis=None, bitorder="big"):
        a = np.asarray(a)
        if axis is not None and axis not in (-1, a.ndim - 1):
            raise NotImplementedError("heart_compat.packbits: only the last axis")
        b = (a.ravel() if axis is None else a).astype(bool).astype(np.uint8)
        if bitorder == "little":
            n = b.shape[-1]
            pad = (-n) % 8
            if pad:
                b = np.concatenate([b, np.zeros(b.shape[:-1] + (pad,), np.uint8)], axis=-1)
            b = b.reshape(b.shape[:-1] + (-1, 8))[..., ::-1].reshape(b.shape)
        return op(b) if axis is None else op(b, axis=-1)

    np.unpackbits, np.packbits = unpackbits, packbits
    sys.stderr.write("INFO: heart_compat 给 numpy %s 补上 packbits / unpackbits 的 bitorder\n" % np.__version__)


def _peaks():
    try:
        import scipy.signal as ss
    except Exception:
        return
    if hasattr(ss, "find_peaks"):
        return
    import numpy as np

    def find_peaks(x, distance=None, prominence=None, **kw):
        if kw:
            raise NotImplementedError("heart_compat.find_peaks: only distance / prominence")
        x = np.asarray(x, np.float64)
        n = len(x)
        peaks = []
        i = 1
        while i < n - 1:                                 # 局部极大（平顶取中点，与 scipy 的 _local_maxima_1d 相同）
            if x[i - 1] < x[i]:
                j = i + 1
                while j < n - 1 and x[j] == x[i]:
                    j += 1
                if x[j] < x[i]:
                    peaks.append((i + j - 1) // 2)
                    i = j
            i += 1
        peaks = np.array(peaks, np.intp)
        props = {}
        if distance is not None and len(peaks) > 1:      # 按峰高从高到低保留，去掉距离 < distance 的低峰
            keep = np.ones(len(peaks), bool)
            order = np.argsort(x[peaks])[::-1]          # 与 scipy 同一个默认排序：等高峰的取舍跟着 numpy 的排序走
            for k in order:
                if not keep[k]:
                    continue
                j = k - 1
                while j >= 0 and peaks[k] - peaks[j] < distance:
                    keep[j] = False
                    j -= 1
                j = k + 1
                while j < len(peaks) and peaks[j] - peaks[k] < distance:
                    keep[j] = False
                    j += 1
            peaks = peaks[keep]
        if prominence is not None:
            prom = np.empty(len(peaks))
            for k, p in enumerate(peaks):
                lo, j = x[p], p
                while j >= 0 and x[j] <= x[p]:
                    lo = min(lo, x[j])
                    j -= 1
                ro, j = x[p], p
                while j < n and x[j] <= x[p]:
                    ro = min(ro, x[j])
                    j += 1
                prom[k] = x[p] - max(lo, ro)
            pmin = prominence[0] if isinstance(prominence, (tuple, list)) else prominence
            sel = prom >= pmin
            peaks, props["prominences"] = peaks[sel], prom[sel]
        return peaks, props

    ss.find_peaks = find_peaks
    sys.stderr.write("INFO: heart_compat 给 scipy 补上 signal.find_peaks（distance / prominence）\n")
