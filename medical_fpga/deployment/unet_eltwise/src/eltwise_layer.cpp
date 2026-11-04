#include "eltwise_layer.hpp"

// Add / Concat / MaxPool for the mixed-precision U-Net (integer_spec.md):
//   Add     both branches scaled into one INT64-exact sum, rounded once.
//   Concat  each branch rescaled separately, channels of a then b.
//   MaxPool 2x2 stride 2, keeps the input scale (no requantization).
// Rounding is round-to-nearest, ties to even, identical to unet_conv.

typedef ap_int<16> act_t;

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
#pragma HLS INTERFACE m_axi port=in_a offset=slave bundle=gmem_a depth=262144 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=in_b offset=slave bundle=gmem_b depth=262144 max_read_burst_length=64
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

    // One row of 128-bit words; b also holds a pooled output row.
    static act_word_t buf_a[MAX_W * MAX_G];
    static act_word_t buf_b[MAX_W * MAX_G];

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
        int row_a = 0, row_b = 0, row_o = 0;
CONCAT_ROWS:
        for (int y = 0; y < h; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=512
LOAD_A:
            for (int n = 0; n < w * ga; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
                buf_a[n] = in_a[row_a + n];
            }
LOAD_B:
            for (int n = 0; n < w * gb; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
                buf_b[n] = in_b[row_b + n];
            }
            int ia = 0, ib = 0, g = 0;
MERGE:
            for (int n = 0; n < w * go; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2048
#pragma HLS PIPELINE II=1
                const bool from_a = g < ga;
                const act_word_t src = from_a ? buf_a[ia] : buf_b[ib];
                const ap_int<32> m = from_a ? ma : mb;
                act_word_t r;
                for (int i = 0; i < LANES; ++i) {
#pragma HLS UNROLL
                    const ap_int<50> v = lane(src, i) * m;
                    r.range(16 * i + 15, 16 * i) = round_clamp(v, shift_bits, qmin, qmax).range(15, 0);
                }
                out[row_o + n] = r;
                if (from_a) ++ia; else ++ib;
                if (++g == go) g = 0;
            }
            row_a += w * ga;
            row_b += w * gb;
            row_o += w * go;
        }
    } else {
        const int row_words = w * ga;
        const int out_words = (w / 2) * ga;
        int row_in = 0, row_o = 0;
POOL_ROWS:
        for (int y = 0; y < h / 2; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=256
LOAD_TOP:
            for (int n = 0; n < row_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=2048
#pragma HLS PIPELINE II=1
                buf_a[n] = in_a[row_in + n];
            }
            act_word_t hold[MAX_G];
#pragma HLS ARRAY_PARTITION variable=hold complete
            int x = 0, g = 0, o = 0;
POOL:
            for (int n = 0; n < row_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=2048
#pragma HLS PIPELINE II=1
                // Vertical max of rows 2y and 2y+1, then horizontal max of x pairs.
                const act_word_t v = lane_max(in_a[row_in + row_words + n], buf_a[n]);
                if ((x & 1) == 0) {
                    hold[g] = v;
                } else {
                    buf_b[o++] = lane_max(hold[g], v);
                }
                if (++g == ga) { g = 0; ++x; }
            }
STORE_POOL:
            for (int n = 0; n < out_words; ++n) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=1024
#pragma HLS PIPELINE II=1
                out[row_o + n] = buf_b[n];
            }
            row_in += 2 * row_words;
            row_o += out_words;
        }
    }
    return ELT_OK;
}
