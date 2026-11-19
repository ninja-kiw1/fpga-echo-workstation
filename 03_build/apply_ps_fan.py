#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_ps_fan.py -- 把 P15 的扇形裁剪（几何逐帧自标定）落到 11-13 的服务端源码上。

只改两个文件：src/unet_ai.c、src/unet_ai.h。所有插入都以唯一锚点定位，
重复运行会因锚点已被改而报错（幂等检查：已含 C_FAN_ON 就跳过）。

用法：python apply_ps_fan.py <src_dir>
"""
import io
import os
import sys

FAN_DEFINES = """#define C_FAN_ON     0x84u    /* P15 扇形裁剪：接在最后，老参数的偏移一个都不动 */
#define C_FAN_XA0    0x8Cu
#define C_FAN_XAG    0x94u
#define C_FAN_XB0    0x9Cu
#define C_FAN_XBG    0xA4u
#define C_FAN_Y0     0xACu
"""

FAN_BLOCK = r"""/* ------------------------------------------------------------------ */
/*  扇形裁剪（P15）：几何**逐帧从图像自己标定**                          */
/*                                                                     */
/*  心超图扇形外是纯黑，而「扇形内要算对」需要的外围像素也有限，逐层反  */
/*  推下来每个 conv 节点每行只需要一段连续列区间。于是每条带只下发 6 个  */
/*  寄存器，kernel 按「绝对输出行号」线性求区间：                       */
/*      x0(y) = xa0 - ((xag*y)>>8)      x1(y) = xb0 + ((xbg*y)>>8)      */
/*  **默认关闭**（g_fan_on = 0）；只有心超 job 会打开，所以眼底/皮肤/  */
/*  CT / 512 档全部逐位不变。                                           */
/* ------------------------------------------------------------------ */
static int g_fan_on = 0;

/* 几何余量：**按每个节点自己的分辨率**给（不能随缩放缩水 —— 112 档的 2 px
 * 到 56 只剩 1 px、28 只剩半个，实测那会让 56/28 层少算边界一圈）。 */
#define FAN_GMARGIN  5
#define FAN_FIX_CX   510        /* 0.498 * 1024 */
#define FAN_FIX_AY   336        /* 0.328 * 1024 */
#define FAN_FIX_TAN  341        /* 0.333 * 1024 */

static int g_fan_hw0_q8 = 0;    /* 第 0 行的半宽（Q8 像素，标定分辨率下） */
static int g_fan_tq     = 0;    /* 每行增长（Q8 像素/行） */
static int g_fan_cx_px  = 0;    /* 中心 x（标定分辨率下） */
static int g_fan_in_w   = 0;    /* 标定时的输入边长 */

/* 每行的列区间在「扇形 ⊕ d」之外再多给几像素，吸收整数式的截断误差
 * （cx/ay 取整 + Q8 斜率 + base 向下取整 + 每行 >>8 向下取整）。 */
static int fan_pad(int w) { return (w > 64) ? 2 : 3; }

static int fan_fixed_mode(void) { return getenv("UNET_FAN_TAN") != NULL; }

/* 固定几何模式（对照/兜底）：比例来自 37° 那台机器的实测 */
static void fan_set_fixed(int w, int h)
{
    const char *e;
    int cxs = FAN_FIX_CX, ays = FAN_FIX_AY, tns = FAN_FIX_TAN;
    int ay;

    (void)h;
    if ((e = getenv("UNET_FAN_CX"))  != NULL) { cxs = atoi(e); }
    if ((e = getenv("UNET_FAN_AY"))  != NULL) { ays = atoi(e); }
    if ((e = getenv("UNET_FAN_TAN")) != NULL) { tns = atoi(e); }
    g_fan_tq = (int)(((long)tns * 256 + 512) >> 10);
    if (g_fan_tq < 0)   { g_fan_tq = 0; }
    if (g_fan_tq > 256) { g_fan_tq = 256; }
    ay = (int)(((long)ays * (long)w + 512) >> 10);
    g_fan_hw0_q8 = ay * g_fan_tq;                       /* A*T 的 Q8 像素 */
    g_fan_cx_px  = (int)(((long)cxs * (long)w + 512) >> 10);
    g_fan_in_w   = w;
}

/* 逐帧标定；返回 0 成功，-1 失败（调用方据此整帧不裁） */
static int fan_calib(const uint8_t *rgb, int w, int h)
{
    int hwbuf[512];
    int y, x, ny = 0, ymin = -1, ymax = -1, ypeak = -1, hwpeak = -1;
    long sy = 0, sh = 0, syy = 0, shy = 0;

    if (rgb == NULL || w < 8 || h < 8 || w > 512 || h > 512) {
        return -1;
    }
    for (y = 0; y < h; y++) {
        const uint8_t *row = rgb + (size_t)y * (size_t)w * 3u;
        int lo = w, hi = -1;
        int ch = w / 2;

        for (x = 0; x < w; x++) {
            if ((row[3 * x] | row[3 * x + 1] | row[3 * x + 2]) != 0) {
                if (x < lo) { lo = x; }
                hi = x;
            }
        }
        if (hi < 0) {
            hwbuf[y] = -1;
            continue;
        }
        {
            const int a = (ch > lo) ? (ch - lo) : (lo - ch);
            const int b = (hi > ch) ? (hi - ch) : (ch - hi);

            hwbuf[y] = (a > b) ? a : b;
        }
        if (ymin < 0) { ymin = y; }
        ymax = y;
        if (hwbuf[y] > hwpeak) { hwpeak = hwbuf[y]; ypeak = y; }
        /* 斜率只在"外弧还没切进来"的那段拟合（y <= ypeak），否则外弧会把斜率
         * 拖小、截距拖大 —— 实测那样锥在顶部虚胖到 15 px 半宽，白白多算一大圈。 */
        if (ypeak < 0 || y <= ypeak) {
            sy += y; sh += hwbuf[y]; syy += (long)y * y; shy += (long)y * hwbuf[y];
            ny++;
        }
    }
    if (ny < h / 8 || ymin < 0 || ypeak <= ymin || ymax < h / 2) {
        return -1;                          /* 内容太少 / 扇形没占到半幅 → 不裁 */
    }
    {
        const long den = (long)ny * syy - sy * sy;
        const long num = (long)ny * shy - sy * sh;
        long b_q8, worst = -1000000L, a_q8;

        if (den == 0) {
            return -1;
        }
        b_q8 = (num * 256 + (num >= 0 ? den / 2 : -(den / 2))) / den;
        if (b_q8 <= 8 || b_q8 > 256) {                  /* 不合理：< 0.03 或 > 1 px/行 */
            return -1;
        }
        for (y = 0; y < h; y++) {
            long v;

            if (hwbuf[y] < 0) continue;
            v = (long)hwbuf[y] * 256 - b_q8 * y;
            if (v > worst) { worst = v; }
        }
        a_q8 = worst;                /* 整体上移 → 支配所有行（余量在 fan_params 里按层加） */
        if (a_q8 < 0) { a_q8 = 0; }
        g_fan_tq     = (int)b_q8;
        g_fan_hw0_q8 = (int)a_q8;
        g_fan_cx_px  = w / 2;
        g_fan_in_w   = w;
    }
    return 0;
}

/* 把标定结果换算到某个分辨率（每个节点按自己的输出尺寸算） */
static void fan_params(int w, int h, int *cx, int *ay, int *tanq8)
{
    const int W = (g_fan_in_w > 0) ? g_fan_in_w : w;
    long hw0n;

    (void)h;
    *tanq8 = g_fan_tq;
    *cx    = (int)(((long)g_fan_cx_px * w + W / 2) / W);
    hw0n   = ((long)g_fan_hw0_q8 * w + W / 2) / W + (long)FAN_GMARGIN * 256;
    *ay    = (g_fan_tq > 0) ? (int)(hw0n / g_fan_tq) : 0;   /* 行 */
}

/* 每个 conv 节点要不要裁、膨胀几像素（-1 = 不裁）。**全流程**：三层分辨率的
 * conv 节点全带上（P15）。数值来自「在扇形上逐层反推每层必须算的集合，再求最小
 * 的 d 使 扇形⊕d 包住它」。
 *
 * 不裁的三类：
 *   - eltwise（5/10 池化、17/24 Concat）：没改 eltwise kernel，保持全宽 = 超集；
 *   - 转置上采样（15/22）：kernel 对 transposed 直接退回全宽；
 *   - 被吸收的 Add（4/9/14/21/28）：它们根本不单独跑。
 */
static const signed char g_fan_d[UNET_NODES_LEN] = {
    -1, 22, 21, 21, -1, -1, 10,  9,  9, -1,
    -1,  4,  3,  3, -1, -1,  4, -1,  3,  2,
     2, -1, -1,  2, -1,  1,  0,  0, -1,  0,
};

int unet_set_fan(int on)
{
    /* UNET_FAN_OFF=1：一刀关掉（A/B 对照用，不用换比特流也不用重编） */
    g_fan_on = (on != 0 && getenv("UNET_FAN_OFF") == NULL) ? 1 : 0;
    return 0;
}

/* 这套模型是「心超扇形图」吗？—— 只有扇形图才能做扇形裁剪。
 * 模型表（unet_models.c）：0=fundus，2=echo_lv，3=echo_inter，4=echo_seq，16=echonet_lv；
 * 其余是空槽。 */
int unet_model_is_sector(int idx)
{
    return (idx == 2 || idx == 3 || idx == 4 || idx == 16) ? 1 : 0;
}

"""

CALIB = r"""    fill_input_tensor(rgb);
    /* P15：**逐帧标定扇形几何**（从这一帧自己的黑边反推），失败就整帧不裁。
     * 调用方（handle_v6c / job_unet_hw）每帧都会重新 unet_set_fan(1)，所以这里
     * 把 g_fan_on 改小不会被带到下一帧。 */
    if (g_fan_on != 0) {
        if (fan_fixed_mode()) {
            fan_set_fixed(g_in_w, g_in_h);
        } else if (fan_calib(rgb, g_in_w, g_in_h) != 0) {
            g_fan_on = 0;
            if (getenv("UNET_DEBUG") != NULL) {
                printf("[ai] fan: 本帧标定失败（内容太少/斜率不合理），整帧不裁\n");
            }
        }
    }"""

FAN_NODE = r"""        /* ---- P15：本节点的扇形列区间参数（每条带只下发一次，按绝对行号） ----
         *   x0(y) = cx - ay*T - y*T - d - 1
         *   x1(y) = cx + ay*T + y*T + d + 1
         * 末尾那个 ±1 是多给的安全余量（掩码反推本身已含 3x3 的膨胀）。
         * d<0 / 未打开 / 转置模式 → fan_on=0，kernel 走原来那条整行路径。 */
        int fan_use = 0, fan_xa0 = 0, fan_xag = 0, fan_xb0 = 0, fan_xbg = 0;
        if (g_fan_on != 0 && g_fan_d[idx] >= 0 && n->mode != CONV_MODE_TCONV2) {
            int cx, ay, tq;
            const int d = (int)g_fan_d[idx];
            const int pad = fan_pad(O->w);

            fan_params(O->w, O->h, &cx, &ay, &tq);
            fan_xag = tq;
            fan_xbg = tq;
            /* 左边界向下取整、右边界向上取整 —— 两边都往"宽"里走 */
            {
                const int bL = (int)(((long)ay * (long)tq) >> 8);
                const int bR = (int)(((long)ay * (long)tq + 255) >> 8);

                /* 第 0 行最窄；窄过 8 像素就整节点不裁（避开 nsteps 极小的退化路径） */
                fan_xa0 = cx - bL - d - pad;
                fan_xb0 = cx + bR + d + pad;
                fan_use = ((bL + d + pad) + (bR + d + pad) >= 8) ? 1 : 0;
            }
            if (getenv("UNET_DEBUG") != NULL) {
                printf("[ai] node %d fan: d=%d cx=%d ay=%d T=%d/256 xa0=%d xb0=%d\n",
                       idx, d, cx, ay, tq, fan_xa0, fan_xb0);
            }
        }

"""

FAN_WR = r"""            wr(g_conv, C_QMAX,  (uint32_t)(int32_t)n->qmax);
            /* P15 扇形裁剪：fan_use=0 时写全 0，kernel 里 fan 标志为假 → 与原行为逐位相同 */
            wr(g_conv, C_FAN_ON,  (uint32_t)fan_use);
            wr(g_conv, C_FAN_XA0, (uint32_t)fan_xa0);
            wr(g_conv, C_FAN_XAG, (uint32_t)fan_xag);
            wr(g_conv, C_FAN_XB0, (uint32_t)fan_xb0);
            wr(g_conv, C_FAN_XBG, (uint32_t)fan_xbg);
            wr(g_conv, C_FAN_Y0,  fan_use ? (uint32_t)y0 : 0u);"""

HDR_DECL = r"""
/**
 * @brief Enable / disable the sector (fan) column trimming on the next runs.
 *
 * 心超图扇形外是纯黑；打开后每个 conv 节点每行只算「必须算」的那段列，
 * 几何**逐帧从输入图像自己标定**（失败则整帧不裁）。默认关闭，
 * 只有扇形图（模型 2/3/4/16）会打开，其它模型/分辨率逐位不变。
 * 环境变量：UNET_FAN_OFF=1 强制关闭（A/B 对照），UNET_FAN_TAN 走固定几何。
 *
 * @return 0.
 */
int  unet_set_fan(int on);

/** @brief 1 if built-in model `idx` is a sector image model (2/3/4/16). */
int  unet_model_is_sector(int idx);
"""


def patch(path, pairs):
    with io.open(path, "r", encoding="utf-8") as f:      # universal newlines -> \n
        s = f.read()
    for old, new, why in pairs:
        if s.count(old) != 1:
            raise SystemExit("%s: anchor %r found %d times (%s)"
                             % (os.path.basename(path), old[:40], s.count(old), why))
        s = s.replace(old, new, 1)
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:   # 统一 CRLF
        f.write(s)
    print("patched", path)


def main():
    src = sys.argv[1]
    c = os.path.join(src, "unet_ai.c")
    h = os.path.join(src, "unet_ai.h")

    with io.open(c, "r", encoding="utf-8") as f:
        body = f.read()
    if "C_FAN_ON" in body:
        print("already patched, nothing to do")
        return

    # 1) C_FAN_* 寄存器偏移（接在 C_QMAX 之后；偏移在 csynth 后按 control_s_axi 核对）
    old_def = "#define C_QMAX       0x7Cu"
    new_def = old_def + "\n" + FAN_DEFINES.rstrip("\n")
    # 2) 扇形几何/标定/参数表整块，插在 CONV 节点之前
    anchor_block = "/* ------------------------------------------------------------------ */\n/*  CONV 节点: 按输出行切条带"
    # 3) unet_run: 填完输入张量后逐帧标定
    anchor_calib = "    fill_input_tensor(rgb);"
    # 4) run_conv_body: 条带循环之前算本节点区间（锚在 conv 的 dbuf 调试打印之后）
    anchor_node = ('            printf("[ai]   conv S=%d dbuf: in=%u x2 out=%u x2 par=%u rin=%d rout=%d\\n",\n'
                   '                   S, in_words, out_words, par_off, rin, rout);\n'
                   '        }\n')
    # 5) 每条带起 IP 前下发 6 个寄存器
    anchor_wr = "            wr(g_conv, C_QMAX,  (uint32_t)(int32_t)n->qmax);"

    patch(c, [
        (old_def, new_def, "C_FAN defines"),
        (anchor_block, FAN_BLOCK + anchor_block, "fan block"),
        (anchor_calib, CALIB, "per-frame calibration"),
        (anchor_node, anchor_node + FAN_NODE, "per-node fan params"),
        (anchor_wr, FAN_WR, "fan register writes"),
    ])

    patch(h, [("\n#endif /* UNET_AI_H */", HDR_DECL + "\n#endif /* UNET_AI_H */", "header decls")])


if __name__ == "__main__":
    main()
