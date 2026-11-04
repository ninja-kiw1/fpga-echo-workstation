#include "mean_filter.hpp"

// Replicate padding, 3x3 unsigned mean, integer floor division.
// Row buffers hold the preceding two rows; no full-frame on-chip buffer.
void mean_filter(const ap_uint<8>* input, ap_uint<8>* output, int width, int height) {
#pragma HLS INTERFACE m_axi port=input offset=slave bundle=gmem_in depth=423500
#pragma HLS INTERFACE m_axi port=output offset=slave bundle=gmem_out depth=423500
#pragma HLS INTERFACE s_axilite port=input bundle=control
#pragma HLS INTERFACE s_axilite port=output bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if (width < 1 || width > MAX_WIDTH || height < 1 || height > MAX_HEIGHT) return;
    ap_uint<8> previous[MAX_WIDTH];
    ap_uint<8> older[MAX_WIDTH];
#pragma HLS BIND_STORAGE variable=previous type=ram_2p impl=bram
#pragma HLS BIND_STORAGE variable=older type=ram_2p impl=bram
    ap_uint<8> window[3][3];
#pragma HLS ARRAY_PARTITION variable=window complete dim=0
    ap_uint<8> top = 0, middle = 0, bottom = 0;
ROW:
    for (int y = 0; y <= height; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=606
COLUMN:
        for (int x = 0; x <= width; ++x) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=701
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=previous inter false
#pragma HLS DEPENDENCE variable=older inter false
            if (x < width) {
                bottom = (y < height) ? input[y * width + x] : previous[x];
                middle = (y == 0) ? bottom : previous[x];
                top = (y <= 1) ? middle : older[x];
                if (y < height) {
                    older[x] = middle;
                    previous[x] = bottom;
                }
            }
            // At x == width, reuse the last column for right padding.
            ap_uint<8> column[3] = {top, middle, bottom};
#pragma HLS ARRAY_PARTITION variable=column complete
SHIFT:
            for (int r = 0; r < 3; ++r) {
#pragma HLS UNROLL
                if (x == 0) {
                    window[r][0] = column[r];
                    window[r][1] = column[r];
                } else {
                    window[r][0] = window[r][1];
                    window[r][1] = window[r][2];
                }
                window[r][2] = column[r];
            }
            if (y > 0 && x > 0) {
                ap_uint<12> sum = 0; // maximum 9 * 255 = 2295
SUM_R:
                for (int r = 0; r < 3; ++r) {
#pragma HLS UNROLL
SUM_C:
                    for (int c = 0; c < 3; ++c) {
#pragma HLS UNROLL
                        sum += window[r][c];
                    }
                }
                output[(y - 1) * width + x - 1] = sum / 9;
            }
        }
    }
}
