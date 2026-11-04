// conv_layer.cpp —— 并行化版本之八 (V12)：V9b + **LOAD/STORE 合并成一趟**（配真双口 BRAM）
//
// 与原版（PLD deployment/unet_conv/src/conv_layer.cpp）功能完全等价：
//   同样的接口 / 寄存器映射 / 内存布局（128bit 字 = 8 个 int16 lane）/
//   同样的整数语义（累加是 mod 2^32 加法 → 结合律成立，重排累加顺序逐位等价）。
//
// ══ 这一版只改一件事：把"回写本行输出"和"预取下一行输入"合并成同一条 streaming 循环 ══
// V9b 每行 `LOAD(548) + init(48) + STEP_ALL(2365) + STORE(515) = 3476` 拍。
// 张量 BRAM 是**单口**时，每行至少 512 读 + 512 写 = 1024 次访问 → 硬件下限 ~1024 拍，
// 而 V9b 的 548+515=1063 已经贴着下限，**分两趟还是合一趟都一样**（单口一拍只能一次访问）。
//
// 但**把张量 BRAM 改成真双口**之后（`blk_mem_gen` True_Dual_Port_RAM +
// `axi_bram_ctrl` SINGLE_PORT_BRAM=0），一拍能同时一读一写 → 合并这一趟可以做到
// ~max(512,512)+prologue ≈ 550 拍，每行 3476 → **~2960**，约省 15%。
//
// ⚠ 两个必须守住的边界（都是前面几版上板实测换来的）：
//   ① **不要把 m_axi 折进计算流水线**（V10 读写都折 = 慢 1.63×，V11 只折回写 = 慢 1.52×）。
//      代价来自"把 m_axi 放进那条流水线"本身，与读写混合无关。所以这一趟是**独立循环**，
//      在 STEP_ALL 之后，和 V9b 的 LOAD/STORE 一样是纯 streaming。
//   ② 预取写的是**本行用完就退役的那个行槽**（`(base_row+kk)%3`），而它发生在 STEP_ALL
//      **之后**，所以不像 V10 那样需要"落后填充 2 列"的取巧 —— 没有任何读写同一槽的冲突。
//
// ══ 其余结构与 V9b 完全一致（已上板验证 ap 353.7ms / 1.502×）══════════════════
//   * 整行一条流水线；slot = [0,work) 计算 / [0,nfill) 补窗 / [0,nq) requantize+存回
//   * 滑窗 = 循环槽位 + A/B 双份 LUTRAM（列索引走 RAM 地址不产生大 mux；
//     两份各出一个读口，绕开 ram_2p 只有 1 读 1 写的限制）
//   * raw 双份按 s&1 轮转（requantize 读上一步、计算写本步，必须物理分开）
//   * 三模式统一：`(krows,sft,KC,PER)` = 3×3 (3,2,4,3) / 1×1 (1,2,2,3) / 转置 (1,1,1,6)
//
// ⚠ 本版按 XU=2 写死相位（sft=2 时 PER=3、转置时 PER=6）。换 XU 要重算。
#if XU != 2
#error "conv_layer_v12 requires XU == 2"
#endif
// r1（2026-10-13）：与 ai/opt_hls/src/conv_layer.cpp（V12）只差三条 m_axi pragma ——
//   突发 64 → 16、在途 16 → 4，FIFO 深度 1024 → 64，目标是把每口 8 个 BRAM18 的缓冲挪到 LUTRAM。
//   这三个口只访问片上张量 BRAM（延迟十几拍），64 拍在途足够盖住延迟。
#include "conv_layer.hpp"

typedef ap_int<16> act_t;
typedef ap_int<8> wgt_t;
typedef ap_int<32> acc_t;

static act_t requantize(acc_t acc, bool relu, ap_int<32> mult, ap_int<64> offset,
                        ap_uint<5> shift, int qmin, int qmax) {
#pragma HLS INLINE
    acc_t a = (relu && acc < 0) ? acc_t(0) : acc;
    ap_int<64> product = a * mult;
    ap_int<65> v = ap_int<65>(product) + offset;
    bool negative = v < 0;
    ap_uint<65> magnitude = negative ? ap_uint<65>(-v) : ap_uint<65>(v);
    ap_uint<65> quotient = magnitude >> shift;
    ap_uint<65> remainder = magnitude & ((ap_uint<65>(1) << shift) - 1);
    ap_uint<65> half = (ap_uint<65>(1) << shift) >> 1;
    // shift == 0 has no fractional part; ties go to the even quotient.
    bool round_up = shift != 0 && (remainder > half || (remainder == half && quotient[0] == 1));
    ap_int<67> rounded = ap_int<67>(quotient) + (round_up ? 1 : 0);
    if (negative) rounded = -rounded;
    if (rounded < qmin) rounded = qmin;
    if (rounded > qmax) rounded = qmax;
    return act_t(rounded);
}

int conv_layer(const act_word_t* in, act_word_t* out, const param_word_t* params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax) {
#pragma HLS INTERFACE m_axi port=in offset=slave bundle=gmem_in depth=65536 max_read_burst_length=16 num_read_outstanding=4
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_out depth=131072 max_write_burst_length=16 num_write_outstanding=4
#pragma HLS INTERFACE m_axi port=params offset=slave bundle=gmem_par depth=2048 max_read_burst_length=16 num_read_outstanding=4
#pragma HLS INTERFACE s_axilite port=in bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=params bundle=control
#pragma HLS INTERFACE s_axilite port=mode bundle=control
#pragma HLS INTERFACE s_axilite port=cin bundle=control
#pragma HLS INTERFACE s_axilite port=cout bundle=control
#pragma HLS INTERFACE s_axilite port=in_h bundle=control
#pragma HLS INTERFACE s_axilite port=in_w bundle=control
#pragma HLS INTERFACE s_axilite port=relu bundle=control
#pragma HLS INTERFACE s_axilite port=shift bundle=control
#pragma HLS INTERFACE s_axilite port=qmin bundle=control
#pragma HLS INTERFACE s_axilite port=qmax bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    if (mode < MODE_CONV3 || mode > MODE_TCONV2 || cin < 1 || cin > MAX_C ||
        cout < 1 || cout > MAX_C || in_h < 1 || in_w < 1 || (relu != 0 && relu != 1) ||
        shift < 0 || shift > MAX_SHIFT || qmin < -32768 || qmax > 32767 || qmin > qmax)
        return CONV_BAD_CONFIG;
    const bool transposed = mode == MODE_TCONV2;
    if (in_h > (transposed ? MAX_H / 2 : MAX_H) || in_w > (transposed ? MAX_W / 2 : MAX_W))
        return CONV_BAD_CONFIG;

    const int cig = (cin + LANES - 1) / LANES;
    const int cog = (cout + LANES - 1) / LANES;
    const int kk = mode == MODE_CONV3 ? 3 : 1;
    const int taps = mode == MODE_CONV3 ? 9 : (mode == MODE_CONV1 ? 1 : 4);
    const int out_h = transposed ? 2 * in_h : in_h;
    const int out_w = transposed ? 2 * in_w : in_w;
    const ap_uint<5> shift_bits = shift;
    const bool use_relu = relu != 0;

    // 行缓冲：形状与原版**完全一样**（不分 bank）——它只负责"每拍喂 1 列"。
    static act_t rows[3][MAX_W][MAX_G][LANES];
#pragma HLS ARRAY_PARTITION variable=rows complete dim=1
#pragma HLS ARRAY_PARTITION variable=rows complete dim=4
    static act_t orow[MAX_W][MAX_G][LANES];
#pragma HLS ARRAY_PARTITION variable=orow complete dim=3
    static wgt_t wbuf[MAX_TAPS][MAX_G][MAX_G][LANES][LANES];
#pragma HLS ARRAY_PARTITION variable=wbuf complete dim=4
#pragma HLS ARRAY_PARTITION variable=wbuf complete dim=5
    static acc_t bias_buf[MAX_G][LANES];
    static ap_int<32> mult_buf[MAX_G][LANES];
    static ap_int<64> offset_buf[MAX_G][LANES];
#pragma HLS ARRAY_PARTITION variable=bias_buf complete dim=2
#pragma HLS ARRAY_PARTITION variable=mult_buf complete dim=2
#pragma HLS ARRAY_PARTITION variable=offset_buf complete dim=2

    // ── 滑窗：循环槽位 LUTRAM，A/B 双份 ────────────────────────────────────
    //   wst[r][slot][g][i] = 输入行 r、物理槽位 slot 的 8 个 lane。
    //   ★ 只切 dim1(行) 与 dim4(lane)：列/组留在 RAM 地址里 → 列索引是**地址**而不是
    //     寄存器选择，不产生大 mux（这是 V6a 爆到 102% LUT 的根因）。
    //   ★ A/B 两份内容恒相同：u=0 读 A、u=1 读 B —— 一条 load 一块 RAM，
    //     避开 ram_2p 只有 1 个读口的限制（这是 V6 卡在 II=2 的根因）。
    static act_t wstA[3][2 * XU + 2][MAX_G][LANES];
    static act_t wstB[3][2 * XU + 2][MAX_G][LANES];
#pragma HLS ARRAY_PARTITION variable=wstA complete dim=1
#pragma HLS ARRAY_PARTITION variable=wstA complete dim=4
#pragma HLS ARRAY_PARTITION variable=wstB complete dim=1
#pragma HLS ARRAY_PARTITION variable=wstB complete dim=4

    // 累加器暂存（**双份，按 s&1 轮转**）与 requantize 结果暂存。
    //   ⚠ raw 必须双份：requantize 读的是**上一步**的 raw，而计算是在**本步**的
    //     slot `work-1` 写 raw。当 nq >= work 时（1x1 的 cog=cig=1 就是 work=1,nq=2），
    //     requantize 的 slot 段会盖过本步那次写入 → 读到被覆盖的值（csim 一次挂 7 个用例，
    //     全是 kk=1 的档）。双份之后"本步写 A 份、上一步的值在 B 份"，永远不冲突。
    //   数据量很小（2份 x 4 x 2 x 8 x 32b = 4Kb），而且同一拍要"读旧值 + 写新值"，
    //   所以用真寄存器（逐维 complete）。
    static acc_t raw[2][MAX_G][XU][LANES];
#pragma HLS ARRAY_PARTITION variable=raw complete dim=1
#pragma HLS ARRAY_PARTITION variable=raw complete dim=2
#pragma HLS ARRAY_PARTITION variable=raw complete dim=3
#pragma HLS ARRAY_PARTITION variable=raw complete dim=4
    static act_t resA[XU][MAX_G][LANES];
#pragma HLS ARRAY_PARTITION variable=resA complete dim=1
#pragma HLS ARRAY_PARTITION variable=resA complete dim=2
#pragma HLS ARRAY_PARTITION variable=resA complete dim=3

    // Weights: word order (tap, og, ig, o), input lanes packed in each word.
    const int weight_words = taps * cog * cig * LANES;
    {
        int t = 0, og = 0, ig = 0, o = 0;
PARAM_WEIGHTS:
        for (int n = 0; n < weight_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=1152
#pragma HLS PIPELINE II=1
            param_word_t word = params[n];
            for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                wbuf[t][og][ig][o][i].range(7, 0) = word.range(8 * i + 7, 8 * i);
            }
            if (++o == LANES) {
                o = 0;
                if (++ig == cig) {
                    ig = 0;
                    if (++og == cog) { og = 0; ++t; }
                }
            }
        }
    }
    const int cop = cog * LANES;
PARAM_CHANNELS:
    for (int c = 0; c < cop; ++c) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=32
#pragma HLS PIPELINE
        ap_int<64> b = params[weight_words + c];
        ap_int<64> m = params[weight_words + cop + c];
        ap_int<64> f = params[weight_words + 2 * cop + c];
        bias_buf[c / LANES][c % LANES] = acc_t(b);
        mult_buf[c / LANES][c % LANES] = ap_int<32>(m);
        offset_buf[c / LANES][c % LANES] = ap_int<64>(f);
    }

    // 窗口的每步推进量 / 计算用到的逻辑列数 / 物理槽位数 / 相位周期
    const int sft  = transposed ? 1 : XU;                    // 每步推进的输入列数
    const int krows = kk;                                    // 窗口要几行
    const int KC   = transposed ? 1 : (XU + kk - 1);         // 计算要用的逻辑列数
    const int WSN  = 2 * XU + 2;                             // 物理槽位数 (6)
    const int PER  = transposed ? 6 : 3;                     // 相位周期 = WSN/gcd(WSN,sft)
    const int col0i = (kk == 3) ? -1 : 0;                    // 步 0 时逻辑列 0 对应的输入列

    // ---- 首次装载：模式 0 要先有输入行 0..kk-2，模式 1/2 只要行 0 ----
    //   之后每行的"新输入行"在**行末与回写合并的那一趟**里预取（见 MERGE 循环）。
    const int need0 = (mode == MODE_CONV3 ? -1 : 0) + kk - 1;
    {
        int rmax = need0;
        if (rmax > in_h - 1) rmax = in_h - 1;
        for (int ry = 0; ry <= rmax; ++ry) {
            const int slot = ry % 3;
            const act_word_t* src = in + ry * in_w * cig;
            int px = 0, g = 0;
            for (int n = 0; n < in_w * cig; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
                act_word_t word = src[n];
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    rows[slot][px][g][i].range(15, 0) = word.range(16 * i + 15, 16 * i);
                }
                if (++g == cig) { g = 0; ++px; }
            }
        }
    }
    // 已经驻留在 rows 里的最高输入行号（转置模式下 base_row 会连续两行相同，用它去重）
    int pf_row = (need0 > in_h - 1) ? (in_h - 1) : need0;

ROWS:
    for (int y = 0; y < out_h; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=512
        // Input rows needed by output row y: base_row .. base_row + kk - 1.
        const int base_row = mode == MODE_CONV3 ? y - 1 : (transposed ? y >> 1 : y);
        // 下一输出行要用的新输入行；它正好落在"本行用完就退役"的那个行槽上
        const int prow = base_row + kk;
        const bool ld_en = (prow <= in_h - 1) && (prow != pf_row);

        // ============ 统一路径：整行一条流水线 ============
        int phase = 0;      // 槽位相位 = s mod PER

        // ---- 滑窗初始化：相位 0 下逻辑列 0..KC-1 落在槽位 0..KC-1 ----
        {
            int ir = 0, ic = 0, ig = 0;
            const int ninit = krows * KC * cig;
            for (int n = 0; n < ninit; ++n) {
#pragma HLS PIPELINE II=1
                const int  col = col0i + ic;
                const int  iy  = base_row + ir;
                const bool v   = (iy >= 0) && (iy < in_h) && (col >= 0) && (col < in_w);
                const int  cc  = v ? col : 0;
                const int  sl  = (iy + 6) % 3;
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    const act_t px = v ? rows[sl][cc][ig][i] : act_t(0);
                    wstA[ir][ic][ig][i] = px;
                    wstB[ir][ic][ig][i] = px;
                }
                if (++ig == cig) { ig = 0; if (++ic == KC) { ic = 0; ++ir; } }
            }
        }

        const int work  = cog * kk * kk * cig;   // 计算 slot 数
        const int nfill = krows * sft * cig;     // 补窗 slot 数（一拍读 1 行）
        const int nq    = XU * cog;              // requantize / 存回 slot 数
        int ncyc = work;
        if (nfill > ncyc) ncyc = nfill;
        if (nq    > ncyc) ncyc = nq;
        const int nsteps = (out_w + XU - 1) / XU;
        const int total  = (nsteps + 2) * ncyc;  // 末尾空转 2 步，把最后两步的结果补写出去

        acc_t acc[XU][LANES];
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=2

        int k = 0;                              // 当前 slot
        int s = 0;                              // 当前 x 步（0..nsteps+1）
        int cg = 0, ky = 0, kx = 0, ig = 0;     // 计算计数器
        int fr = 0, fc = 0, fg = 0;             // 补窗计数器
        int su = 0, sg = 0;                     // requantize/存回计数器
        int wcol0 = col0i;                      // 窗口逻辑列 0 对应的输入列
        int st_x0 = -2 * XU;                    // 正在写 orow 的那一步的 x0（落后两步）

STEP_ALL:
        for (int n = 0; n < total; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=36864
#pragma HLS PIPELINE II=1
            const int basew = sft * phase;      // 本步的槽位相位偏移

            // ---- (1) 计算：第 k 个 (cg,ky,kx,ig)，一拍算 XU 个像素 ----
            if (s < nsteps && k < work) {
                // 逻辑列 c 落在槽位 (c + sft*phase) mod WSN
                int c0 = (transposed ? 0 : kx) + basew;
                if (c0 >= WSN) c0 -= WSN;
                int c1 = (transposed ? 0 : (kx + 1)) + basew;
                if (c1 >= WSN) c1 -= WSN;
                act_t pixel[XU][LANES];
#pragma HLS ARRAY_PARTITION variable=pixel complete dim=1
#pragma HLS ARRAY_PARTITION variable=pixel complete dim=2
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    pixel[0][i] = wstA[ky][c0][ig][i];   // u = 0 -> 读 A 份
                    pixel[1][i] = wstB[ky][c1][ig][i];   // u = 1 -> 读 B 份
                }
                // 权重 tap：3x3 = ky*3+kx；1x1 = 0；转置 = (y&1)*2 + (x&1)，x=x0+u
                const int tap = (kk == 3) ? (ky * 3 + kx) : (transposed ? ((y & 1) << 1) : 0);
                const bool first = (ky == 0) && (kx == 0) && (ig == 0);
                const bool last  = (ky == krows - 1) && (kx == kk - 1) && (ig == cig - 1);
                for (int o = 0; o < LANES; ++o) {
#pragma HLS UNROLL
                    for (int u = 0; u < XU; ++u) {
#pragma HLS UNROLL
                        // 转置：x0 恒为偶，所以 x 的奇偶就是 u 的奇偶
                        const int tu = transposed ? (tap + u) : tap;
                        ap_int<28> dot = 0;
                        for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                            dot += pixel[u][i] * wbuf[tu][cg][ig][o][i];
                        }
                        const acc_t sum = (first ? bias_buf[cg][o] : acc[u][o]) + dot;
                        acc[u][o] = sum;
                        // 只把累加器寄存下来（写本步那一份）；requantize 挪到下面 (3)
                        if (last) raw[s & 1][cg][u][o] = sum;
                    }
                }
                if (++ig == cig) {
                    ig = 0;
                    if (++kx == kk) {
                        kx = 0;
                        if (++ky == krows) { ky = 0; ++cg; }
                    }
                }
            }

            // ---- (2) 补窗：写下一步要用的槽位（与计算槽位不相交）----
            //   ⚠ 一拍**只读一行** rows：行槽位 sl=(iy+6)%3 是运行时索引，HLS 证明不了
            //     多条 load 落在不同分片，一次读多行会被判成撞同一块 RAM → II=2。
            if (s < nsteps && k < nfill) {
                int sf = KC + fc + basew;           // 逻辑列 KC+fc 的槽位
                if (sf >= WSN) sf -= WSN;
                const int  col = wcol0 + KC + fc;   // 对应的输入列
                const int  iy  = base_row + fr;
                const bool v   = (col >= 0) && (col < in_w) && (iy >= 0) && (iy < in_h);
                const int  sl  = (iy + 6) % 3;
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    const act_t px = v ? rows[sl][col][fg][i] : act_t(0);
                    wstA[fr][sf][fg][i] = px;
                    wstB[fr][sf][fg][i] = px;
                }
                if (++fg == cig) { fg = 0; if (++fc == sft) { fc = 0; ++fr; } }
            }

            // ---- (3) requantize(上一步的 raw) + 存回(上上步的 resA) ----
            //   raw 读的是**(s+1)&1 那一份** = 上一步写进去的（见 raw 的声明处说明）；
            //   orow 拿到的是 resA 的**旧值**（硬件先读后写），正好是上上步的结果；
            //   循环末尾多跑 2 步空转把最后两步补上（见 total 的算法）。
            if (k < nq) {
                for (int o = 0; o < LANES; ++o) {
#pragma HLS UNROLL
                    if (st_x0 >= 0 && (st_x0 + su) < out_w)
                        orow[st_x0 + su][sg][o] = resA[su][sg][o];
                    resA[su][sg][o] = requantize(raw[(s + 1) & 1][sg][su][o], use_relu,
                                                 mult_buf[sg][o], offset_buf[sg][o],
                                                 shift_bits, qmin, qmax);
                }
                if (++sg == cog) { sg = 0; ++su; }
            }

            // ---- 步末：推进槽位相位 / 列号（纯计数，不占 slot）----
            if (++k == ncyc) {
                k = 0;
                cg = 0; ky = 0; kx = 0; ig = 0;
                fr = 0; fc = 0; fg = 0;
                su = 0; sg = 0;
                ++s;
                wcol0 += sft;
                st_x0 += XU;
                if (++phase == PER) phase = 0;
            }
        }

        act_word_t* dst = out + y * out_w * cog;
        // ---- 合并的一趟：回写本行输出 + 预取下一行的输入 ----
        //   两个方向**同时**发生，所以这一趟能有多快，取决于张量 BRAM 是不是**真双口**：
        //     单口 → 一拍只能一次访问，合并后总拍数 = 读+写，和分两趟一样（不亏也不赚）；
        //     双口 → 一拍能同时一读一写，搬运拍数直接砍半。
        //   ⚠ **不要**把它折进上面的计算流水线 —— 实测那样会慢 1.5~1.6 倍
        //     （V10/V11，见 README §8.6/§8.7：把 m_axi 折进计算流水线本身就贵）。
        const int pslot = (prow + 6) % 3;          // 目标行退役后的那个槽
        const int psafe = ld_en ? prow : 0;
        const act_word_t* psrc = in + psafe * in_w * cig;
        const int nst_w = out_w * cog;             // 本行要回写的字数
        const int nld_w = ld_en ? (in_w * cig) : 0;// 要预取的字数
        const int nmerge = (nst_w > nld_w) ? nst_w : nld_w;
        int wpx = 0, wg = 0;                       // 回写：像素 / 组
        int lpx = 0, lg = 0;                       // 预取：像素 / 组
MERGE:
        for (int n = 0; n < nmerge; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
            if (n < nst_w) {
                act_word_t word;
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    word.range(16 * i + 15, 16 * i) = orow[wpx][wg][i].range(15, 0);
                }
                dst[n] = word;
                if (++wg == cog) { wg = 0; ++wpx; }
            }
            if (n < nld_w) {
                act_word_t word = psrc[n];
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    rows[pslot][lpx][lg][i].range(15, 0) = word.range(16 * i + 15, 16 * i);
                }
                if (++lg == cig) { lg = 0; ++lpx; }
            }
        }
        if (ld_en) pf_row = prow;
    }
    return CONV_OK;
}
