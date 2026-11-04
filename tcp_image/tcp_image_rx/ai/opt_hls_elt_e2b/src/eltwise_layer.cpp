// eltwise_layer.cpp —— E2b = E2 + gmem_a / gmem_b 的 num_read_outstanding=2（只改这两个 pragma）
//
// E2 上板（10-11）：MaxPool 0.83×（如设计），Concat 却 1.02×（没快）。协同仿真里 Concat 合并循环是
// 1.09 拍/输出字，所以设计本身没问题；板上的原因（推断，与实测 ~1660 拍/趟吻合）：每趟开头 gmem_a、gmem_b
// 两个口各一次性发出最多 16 个 64 拍突发，互连轮流服务 → b 的数据和 a 交替到达、塞进 b 的 1024 拍 FIFO，
// 而循环先只吃 a → 前半趟 a 只拿到一半读带宽（~1536 拍/1024 次迭代）。Add 是 a、b 同步消费，不受影响。
// 把两个读口的在途突发限到 2 个：b 最多先取 128 拍，a 拿到几乎整条读口。逻辑、寄存器映射、端口都不变，
// m_axi 读 FIFO 还变小了（省 BRAM）。
//
// eltwise_layer.cpp —— E2：原版 + Concat / MaxPool 的"读写合并成一趟"（配真双口张量 BRAM）
//
// 与原版（PLD deployment/unet_eltwise/src/eltwise_layer.cpp）功能完全等价：
//   同样的接口 / 寄存器映射 / m_axi 口名（gmem_a、gmem_b、gmem_out）/ 内存布局 /
//   同样的整数语义（round_clamp、lane_max 一字未改）/ 同样的非法配置检查和返回值。
//
// ══ 为什么改 ══
// 10-06 实测（单口 BRAM）eltwise 9 个节点共 48.9 ms，全部卡在 BRAM 访问次数上：
//   Add 3.13 拍/字（2 读 1 写）、Concat 1.10 拍/次访问、MaxPool 1.41 拍/输入字。
// 张量 BRAM 改成真双口（xu2dp）之后一拍能同时一读一写，但原版 Concat / MaxPool
// 是"先读完、再写回"的几趟独立循环，读和写从不同时发生，双口帮不上。
// 本版只改这两个模式的循环结构（与 conv V12 同一个思路），Add 一字未改
// （它本来就是每拍读 a、b 写 out 的一条循环，读口是瓶颈）。
//
// ══ Concat ══
//   行缓冲 buf_a / buf_b 各拆成两半做乒乓：第 k 趟同时"写回第 k-1 行"和"预取第 k 行"，
//   一趟 w·go 拍里每拍一读一写；h 行共 h+1 趟（第 0 趟只预取）。
//   一行放不进半个缓冲时（w·ga 或 w·gb > 1024 字，网络里不出现）退回"读一趟、写一趟"，
//   **用的是同一条循环**（只改上下界和槽位），不复制运算硬件（HLS 不跨循环共享硬件）。
// ══ MaxPool ══
//   每个输出行原来三趟（读上行 / 读下行并取最大 / 写回），现在两趟：
//   "读上行"和"写回上一个输出行"合成一趟；输出行缓冲 buf_b 拆两半乒乓
//   （(w/2)·ga ≤ 1024 字恒成立）。最后一行单独写回一趟。
//
// BRAM 用量与原版相同（只是把原有两块行缓冲各拆成两半）。

#include "eltwise_layer.hpp"

typedef ap_int<16> act_t;

static const int BUF_WORDS = MAX_W * MAX_G;  // 2048
static const int HALF = BUF_WORDS / 2;       // 1024

static act_t lane(const act_word_t& word, int i) {
#pragma HLS INLINE
    act_t v;
    v.range(15, 0) = word.range(16 * i + 15, 16 * i);
    return v;
}

static act_t round_clamp(ap_int<50> v, ap_uint<5> shift, int qmin, int qmax) {
#pragma HLS INLINE
    bool negative = v < 0;
    ap_uint<50> magnitude = negative ? ap_uint<50>(-v) : ap_uint<50>(v);
    ap_uint<50> quotient = magnitude >> shift;
    ap_uint<50> remainder = magnitude & ((ap_uint<50>(1) << shift) - 1);
    ap_uint<50> half = (ap_uint<50>(1) << shift) >> 1;
    bool round_up = shift != 0 && (remainder > half || (remainder == half && quotient[0] == 1));
    ap_int<52> rounded = ap_int<52>(quotient) + (round_up ? 1 : 0);
    if (negative) rounded = -rounded;
    if (rounded < qmin) rounded = qmin;
    if (rounded > qmax) rounded = qmax;
    return act_t(rounded);
}

static act_word_t lane_max(const act_word_t& a, const act_word_t& b) {
#pragma HLS INLINE
    act_word_t r;
    for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
        act_t x = lane(a, i), y = lane(b, i);
        r.range(16 * i + 15, 16 * i) = (x > y ? x : y).range(15, 0);
    }
    return r;
}

int eltwise_layer(const act_word_t* in_a, const act_word_t* in_b, act_word_t* out,
                  int mode, int ca, int cb, int h, int w,
                  int m0, int m1, int shift, int qmin, int qmax) {
#pragma HLS INTERFACE m_axi port=in_a offset=slave bundle=gmem_a depth=262144 max_read_burst_length=64 num_read_outstanding=2
#pragma HLS INTERFACE m_axi port=in_b offset=slave bundle=gmem_b depth=262144 max_read_burst_length=64 num_read_outstanding=2
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_out depth=262144 max_write_burst_length=64
#pragma HLS INTERFACE s_axilite port=in_a bundle=control
#pragma HLS INTERFACE s_axilite port=in_b bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=mode bundle=control
#pragma HLS INTERFACE s_axilite port=ca bundle=control
#pragma HLS INTERFACE s_axilite port=cb bundle=control
#pragma HLS INTERFACE s_axilite port=h bundle=control
#pragma HLS INTERFACE s_axilite port=w bundle=control
#pragma HLS INTERFACE s_axilite port=m0 bundle=control
#pragma HLS INTERFACE s_axilite port=m1 bundle=control
#pragma HLS INTERFACE s_axilite port=shift bundle=control
#pragma HLS INTERFACE s_axilite port=qmin bundle=control
#pragma HLS INTERFACE s_axilite port=qmax bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    if (mode < MODE_ADD || mode > MODE_MAXPOOL || ca < 1 || ca > MAX_C ||
        h < 1 || h > MAX_H || w < 1 || w > MAX_W)
        return ELT_BAD_CONFIG;
    if (mode == MODE_MAXPOOL) {
        if ((h & 1) || (w & 1)) return ELT_BAD_CONFIG;
    } else {
        if (shift < 0 || shift > MAX_SHIFT || qmin < -32768 || qmax > 32767 || qmin > qmax)
            return ELT_BAD_CONFIG;
        if (mode == MODE_ADD && cb != ca) return ELT_BAD_CONFIG;
        if (mode == MODE_CONCAT && ((ca % LANES) != 0 || cb < 1 || ca + cb > MAX_C))
            return ELT_BAD_CONFIG;
    }

    const int ga = (ca + LANES - 1) / LANES;
    const int gb = (cb + LANES - 1) / LANES;
    const ap_int<32> ma = m0, mb = m1;
    const ap_uint<5> shift_bits = shift;

    // 两块行缓冲（与原版同尺寸），各按 [0,HALF) / [HALF,2·HALF) 两半乒乓。
    static act_word_t buf_a[BUF_WORDS];
    static act_word_t buf_b[BUF_WORDS];

    if (mode == MODE_ADD) {
        const int total = h * w * ga;
ADD:
        for (int n = 0; n < total; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=1048576
#pragma HLS PIPELINE II=1
            const act_word_t a = in_a[n], b = in_b[n];
            act_word_t r;
            for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                const ap_int<50> v = ap_int<50>(lane(a, i) * ma) + ap_int<50>(lane(b, i) * mb);
                r.range(16 * i + 15, 16 * i) = round_clamp(v, shift_bits, qmin, qmax).range(15, 0);
            }
            out[n] = r;
        }
    } else if (mode == MODE_CONCAT) {
        const int go = ga + gb;
        const int na = w * ga, nb = w * gb, nrow = w * go;
        // 乒乓：两行都放得进半个缓冲（网络里恒成立：两个 Concat 每行各 512 字）。
        const bool pp = na <= HALF && nb <= HALF;
        const int steps = pp ? h + 1 : 2 * h;
CONCAT_STEPS:
        for (int k = 0; k < steps; ++k) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=1024
            // 本趟：预取第 ld 行到槽 ls、写回第 st 行（来自槽 ss）。
            int ld, st, ls, ss;
            if (pp) {
                ld = k;  st = k - 1;
                ls = (k & 1) ? HALF : 0;
                ss = (k & 1) ? 0 : HALF;
            } else {
                ld = (k & 1) ? h : (k >> 1);   // 偶数趟只读
                st = (k & 1) ? (k >> 1) : -1;  // 奇数趟只写
                ls = 0;  ss = 0;
            }
            const int nld = (ld < h) ? nrow : 0;
            const int nst = (st >= 0) ? nrow : 0;
            const int nmerge = (nld > nst) ? nld : nst;
            const int lda = (ld < h) ? ld : 0, sts = (st >= 0) ? st : 0;
            const act_word_t* src_a = in_a + lda * na;
            const act_word_t* src_b = in_b + lda * nb;
            act_word_t* dst = out + sts * nrow;
            int ia = 0, ib = 0, g = 0;
CONCAT_MERGE:
            for (int n = 0; n < nmerge; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=buf_a type=inter false
#pragma HLS DEPENDENCE variable=buf_b type=inter false
#pragma HLS DEPENDENCE variable=buf_a type=intra false
#pragma HLS DEPENDENCE variable=buf_b type=intra false
                // 写回（读槽 ss）与预取（写槽 ls）：乒乓时两槽不同，不乒乓时分在不同趟。
                if (n < nst) {
                    const bool from_a = g < ga;
                    const act_word_t src = from_a ? buf_a[ss + ia] : buf_b[ss + ib];
                    const ap_int<32> m = from_a ? ma : mb;
                    act_word_t r;
                    for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                        const ap_int<50> v = lane(src, i) * m;
                        r.range(16 * i + 15, 16 * i) = round_clamp(v, shift_bits, qmin, qmax).range(15, 0);
                    }
                    dst[n] = r;
                    if (from_a) ++ia; else ++ib;
                    if (++g == go) g = 0;
                }
                if (n < nld) {
                    if (n < na) buf_a[ls + n] = src_a[n];
                    else        buf_b[ls + n - na] = src_b[n - na];
                }
            }
        }
    } else {
        const int row_words = w * ga;
        const int out_words = (w / 2) * ga;
        const int rows = h / 2;
POOL_ROWS:
        for (int y = 0; y < rows; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=256
            const act_word_t* top = in_a + 2 * y * row_words;
            const act_word_t* bot = top + row_words;
            // 上一个输出行在 buf_b 的另一半（y=0 时没有）。
            const int pslot = (y & 1) ? 0 : HALF;
            const int cslot = (y & 1) ? HALF : 0;
            const int nst = (y > 0) ? out_words : 0;
            act_word_t* pdst = out + ((y > 0) ? (y - 1) : 0) * out_words;
TOP_MERGE:
            for (int n = 0; n < row_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=2048
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=buf_b type=inter false
                buf_a[n] = top[n];
                if (n < nst) pdst[n] = buf_b[pslot + n];
            }
            act_word_t hold[MAX_G];
#pragma HLS ARRAY_PARTITION variable=hold complete
            int x = 0, g = 0, o = 0;
POOL:
            for (int n = 0; n < row_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=2048
#pragma HLS PIPELINE II=1
                // 第 2y 行与第 2y+1 行纵向取最大，再横向两两取最大。
                const act_word_t v = lane_max(bot[n], buf_a[n]);
                if ((x & 1) == 0) {
                    hold[g] = v;
                } else {
                    buf_b[cslot + o++] = lane_max(hold[g], v);
                }
                if (++g == ga) { g = 0; ++x; }
            }
        }
        // 最后一个输出行单独写回。
        const int lslot = ((rows - 1) & 1) ? HALF : 0;
        act_word_t* ldst = out + (rows - 1) * out_words;
STORE_LAST:
        for (int n = 0; n < out_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=1024
#pragma HLS PIPELINE II=1
            ldst[n] = buf_b[lslot + n];
        }
    }
    return ELT_OK;
}
