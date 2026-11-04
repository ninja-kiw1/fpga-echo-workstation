#include "conv_layer_stream.hpp"

// Generic layer engine for the mixed-precision U-Net (mixed_precision_spec.md).
//   MODE_CONV3  3x3, stride 1, zero padding 1
//   MODE_CONV1  1x1, stride 1
//   MODE_TCONV2 2x2 transposed conv, stride 2: output (y, x) is a 1x1 conv of
//               input (y/2, x/2) using weight tap (y%2)*2 + (x%2).
// Per output channel: acc = bias + sum(x*w) in INT32; optional ReLU;
// v = acc*M + offset exactly; round-to-nearest-even shift; clamp to [qmin, qmax].
// Rows are processed as load -> compute -> store; 8x8 MACs per cycle.
//
// 2026-10-01 流接口版：出入口由 m_axi 改为 AXI-Stream（由 PS 推拉），
//   计算部分与原版逐字相同。参数流顺序与原版内存布局一致。

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

int conv_layer(hls::stream<act_word_t>& in, hls::stream<act_word_t>& out,
               hls::stream<param_word_t>& params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax) {
#pragma HLS INTERFACE axis port=in
#pragma HLS INTERFACE axis port=out
#pragma HLS INTERFACE axis port=params
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

    // Three rolling input rows (row r lives in slot r % 3), all channels.
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

    // Weights: word order (tap, og, ig, o), input lanes packed in each word.
    const int weight_words = taps * cog * cig * LANES;
    {
        int t = 0, og = 0, ig = 0, o = 0;
PARAM_WEIGHTS:
        for (int n = 0; n < weight_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=1152
#pragma HLS PIPELINE II=1
            param_word_t word = params.read();
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
    // 参数流顺序与原版内存布局一致：bias 块 -> mult 块 -> offset 块
PARAM_BIAS:
    for (int c = 0; c < cop; ++c) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=32
#pragma HLS PIPELINE
        bias_buf[c / LANES][c % LANES] = acc_t(params.read());
    }
PARAM_MULT:
    for (int c = 0; c < cop; ++c) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=32
#pragma HLS PIPELINE
        mult_buf[c / LANES][c % LANES] = ap_int<32>(ap_int<64>(params.read()));
    }
PARAM_OFFSET:
    for (int c = 0; c < cop; ++c) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=32
#pragma HLS PIPELINE
        offset_buf[c / LANES][c % LANES] = ap_int<64>(params.read());
    }

    int loaded = 0;
ROWS:
    for (int y = 0; y < out_h; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=512
        // Input rows needed by output row y: base_row .. base_row + kk - 1.
        const int base_row = mode == MODE_CONV3 ? y - 1 : (transposed ? y >> 1 : y);
        int need = base_row + kk - 1;
        if (need > in_h - 1) need = in_h - 1;
LOAD_ROWS:
        for (; loaded <= need; ++loaded) {
#pragma HLS LOOP_TRIPCOUNT min=0 max=2
            const int slot = loaded % 3;
            int px = 0, g = 0;
LOAD:
            for (int n = 0; n < in_w * cig; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
                act_word_t word = in.read();
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    rows[slot][px][g][i].range(15, 0) = word.range(16 * i + 15, 16 * i);
                }
                if (++g == cig) { g = 0; ++px; }
            }
        }

        const int slot0 = (base_row + 3) % 3;
        const int slot1 = (base_row + 4) % 3;
        const int slot2 = (base_row + 5) % 3;
        const int work = out_w * cog * kk * kk * cig;
        int x = 0, cg = 0, ky = 0, kx = 0, ig = 0;
        acc_t acc[LANES];
#pragma HLS ARRAY_PARTITION variable=acc complete
COMPUTE:
        for (int n = 0; n < work; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=73728
#pragma HLS PIPELINE II=1
            const int iy = base_row + ky;
            int ix, tap;
            if (mode == MODE_CONV3) { ix = x + kx - 1; tap = ky * 3 + kx; }
            else if (mode == MODE_CONV1) { ix = x; tap = 0; }
            else { ix = x >> 1; tap = ((y & 1) << 1) | (x & 1); }
            const bool valid = iy >= 0 && iy < in_h && ix >= 0 && ix < in_w;
            const int slot = ky == 0 ? slot0 : (ky == 1 ? slot1 : slot2);
            const int col = valid ? ix : 0;
            act_t pixel[LANES];
#pragma HLS ARRAY_PARTITION variable=pixel complete
            for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                pixel[i] = valid ? rows[slot][col][ig][i] : act_t(0);
            }
            const bool first = ky == 0 && kx == 0 && ig == 0;
            const bool last = ky == kk - 1 && kx == kk - 1 && ig == cig - 1;
            for (int o = 0; o < LANES; ++o) {
#pragma HLS UNROLL
                ap_int<28> dot = 0;
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    dot += pixel[i] * wbuf[tap][cg][ig][o][i];
                }
                const acc_t sum = (first ? bias_buf[cg][o] : acc[o]) + dot;
                acc[o] = sum;
                if (last) {
                    orow[x][cg][o] = requantize(sum, use_relu, mult_buf[cg][o],
                                                offset_buf[cg][o], shift_bits, qmin, qmax);
                }
            }
            if (++ig == cig) {
                ig = 0;
                if (++kx == kk) {
                    kx = 0;
                    if (++ky == kk) {
                        ky = 0;
                        if (++cg == cog) { cg = 0; ++x; }
                    }
                }
            }
        }

        int px = 0, g = 0;
STORE:
        for (int n = 0; n < out_w * cog; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
            act_word_t word;
            for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                word.range(16 * i + 15, 16 * i) = orow[px][g][i].range(15, 0);
            }
            out.write(word);
            if (++g == cog) { g = 0; ++px; }
        }
    }
    return CONV_OK;
}
