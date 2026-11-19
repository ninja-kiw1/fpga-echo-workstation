# -*- coding: utf-8 -*-
"""WS_OPT_V2「少回图」补丁 —— 把 workstation.py 改成「回传只留 logit 一幅」。

用法（幂等，打过的会跳过）：
    python 03_build\\apply_fastimg.py 07_ui\\workstation.py
    python 03_build\\apply_fastimg.py 01_src\\ui\\workstation.py

改了什么
--------
上位机原来每帧都要板端回 **预处理图 + 掩膜 + 叠加图(+logit)**，每幅 512x512x3 = 789504 B。
2026-11-19 上板实测（6 张真实 512 图，`04_verify/verify_noimg.py`）：

  * 板端回的 proc **就是发出去的那张原图**（mode 5 原图直通，md5 相同）；
  * mask / overlay 用本文件的 mask_rgb / overlay_rgb 算，与板端回传**逐字节相同**。

所以只留 logit 一幅，其余三幅在本地生成：
  回传 3.15 MB -> 0.79 MB，整次往返 525 -> 395 ms（同一张图、同一块板）。

不满足条件时原样退回老口径：ROI 帧（底图与裁剪相关）、老板端（不认 b5，没有 logit 图）、
没勾掩膜/叠加（本来就省不到东西）、点了「点击纠错」的那一帧（历史栈要存图）。
环境变量 `WS_NO_FASTIMG=1` 可整体关掉。
"""
import hashlib
import os
import re
import sys

MARK = "WS_OPT_V2"

# ---- 1. 新增 _fast_flags 方法（插在 _on_send 之前）----
METHOD = '''    def _fast_flags(self, flags, roi, inter=None):
        """WS_OPT_V2「少回图」：预处理图 / 掩膜 / 叠加图 改由上位机本地生成，回传只留 logit 一幅。

        依据（2026-11-19 上板实测，6 张真实 512 图逐字节 PASS，脚本 04_verify/verify_noimg.py）：
          * 板端回的 proc **就是发出去的那张原图**（mode 5 原图直通，md5 相同）；
          * mask / overlay 用本文件的 mask_rgb / overlay_rgb 算，与板端回传**逐字节相同**
            （`_load_logits` 里那条「与板端逐字节一致 ✓」自检一直在跑这件事）。
        回传 3.15 MB -> 0.79 MB，一次往返 525 -> 395 ms。条件不满足就原样返回：
        ROI 帧（底图与裁剪相关）、老板端（不认 b5 就没有 logit 图）、
        没勾掩膜/叠加（本来就省不到）、点击纠错帧（历史栈里要存原样）。
        环境变量 WS_NO_FASTIMG=1 可整体关掉。
        """
        self.fast_noimg = False
        if os.environ.get("WS_NO_FASTIMG"):
            return flags
        if roi is not None or inter is not None or self.inter is not None:
            return flags
        ver = self.board_stat_version
        if ver is not None and ver < proto.AI_STAT_VERSION_LOGIT:
            return flags
        if not flags & (proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER):
            return flags
        self.fast_noimg = True
        return ((flags & ~(proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER))
                | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS)

'''

# ---- 2. _on_send 里挂钩子 ----
OLD_SEND = '''        elif self.var_inter.get() and self._ui_model().get("inter"):
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS     # 自动结果要当纠错的起点
        self.inflight_roi = roi
'''
NEW_SEND = '''        elif self.var_inter.get() and self._ui_model().get("inter"):
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS     # 自动结果要当纠错的起点
        # WS_OPT_V2 少回图：本地生成用的底图 = 本帧真正发出去的那份字节
        self.inflight_image = image
        flags = self._fast_flags(flags, roi, inter)
        self.inflight_roi = roi
'''

# ---- 3. _on_result 里把 proc 补回 images（下游代码看到的还是老结构）----
OLD_RES = '''    def _on_result(self, images, tim, rowstats, ai_stats, mode, flags):
        self.reply_images = images
'''
NEW_RES = '''    def _on_result(self, images, tim, rowstats, ai_stats, mode, flags):
        if self.fast_noimg and "proc" not in images and self.inflight_image is not None:
            # WS_OPT_V2：这一帧没要预处理图 —— 补上发出去的原图（mode 5 直通，两者逐字节相同），
            # 下游（本地叠加、纠错历史栈、保存回图）看到的字典结构与老口径完全一样。
            images["proc"] = self.inflight_image
        self.reply_images = images
'''

# ---- 4. __init__ 里初始化两个新字段 ----
OLD_INIT = '''        self.image = None          # 当前要发送的原图(bytes)
'''
NEW_INIT = '''        self.image = None          # 当前要发送的原图(bytes)
        self.inflight_image = None                       # WS_OPT_V2：在途那一帧真正发出去的字节
        self.fast_noimg = False                          # WS_OPT_V2：本帧是否走了「少回图」
'''

# ---- 6. 序列跟踪（心超单步/点击纠错）那条路：同样只回 logit ----
OLD_SEQ = '''        self.inflight = what
        flags = proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
'''
NEW_SEQ = '''        self.inflight = what
        # WS_OPT_V2 少回图：掩膜本来就是本地 prior_from_plane 算的，板端那幅 789504 B 只用来
        # 打「与板端逐字节一致」的标记 —— 上板核对过等价之后没必要每帧都传。
        flags = proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
'''

OLD_BADGE = '''            "｜紧凑帧 + 板端跟踪" if s.mask128 is not None else "",
            "与板端逐字节一致 ✓" if s.verified else ("与板端不一致 ✗" if s.verified is False else "")))
'''
NEW_BADGE = '''            "｜紧凑帧 + 板端跟踪" if s.mask128 is not None else "",
            ("与板端逐字节一致 ✓" if s.verified else ("与板端不一致 ✗" if s.verified is False
                                                   else "掩膜本地生成（板端只回 logit）"))))
'''
OLD_NOTE = '''        if name in self.local_views:
            src = "本地按门限 %d 重算，未重跑 FPGA" % self._ui_ai_thr()
'''
NEW_NOTE = '''        if name in self.local_views:
            src = ("本地生成（板端只回 logit 一幅）" if self.fast_noimg
                   else "本地按门限 %d 重算，未重跑 FPGA" % self._ui_ai_thr())
'''


def patch(path):
    with open(path, "rb") as f:
        src = f.read().decode("utf-8")
    if MARK in src:
        print("  %-42s 已打过，跳过" % os.path.basename(path))
        return 0
    n = 0
    for old, new in ((OLD_INIT, NEW_INIT), (OLD_SEND, NEW_SEND), (OLD_RES, NEW_RES), (OLD_NOTE, NEW_NOTE),
                     (OLD_SEQ, NEW_SEQ), (OLD_BADGE, NEW_BADGE)):
        cnt = src.count(old)
        if cnt != 1:
            print("  FAIL %s：锚点出现 %d 次（应为 1）\n  ---\n%s" % (path, cnt, old[:120]))
            return 1
        src = src.replace(old, new)
        n += 1
    anchor = "    def _on_send(self, inter=None):\n"
    if src.count(anchor) != 1:
        print("  FAIL %s：_on_send 锚点 %d 次" % (path, src.count(anchor)))
        return 1
    src = src.replace(anchor, METHOD + anchor)
    n += 1
    with open(path, "wb") as f:
        f.write(src.encode("utf-8"))
    print("  %-42s 打了 %d 处  md5 %s" % (os.path.basename(path),
                                          n, hashlib.md5(src.encode("utf-8")).hexdigest()[:12]))
    return 0


def main():
    paths = sys.argv[1:]
    if not paths:
        here = os.path.dirname(os.path.abspath(__file__))
        paths = [os.path.join(here, "..", "07_ui", "workstation.py"),
                 os.path.join(here, "..", "01_src", "ui", "workstation.py")]
    bad = 0
    for p in paths:
        bad |= patch(os.path.abspath(p))
    return bad


if __name__ == "__main__":
    sys.exit(main())
