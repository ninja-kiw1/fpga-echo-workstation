#include "morphology.hpp"
#include <hls_stream.h>
typedef ap_uint<8> Pixel;

static void read_pixels(const Pixel* input, hls::stream<Pixel>& stream, int count) {
#pragma HLS INLINE off
READ_PIXELS:
    for (int i = 0; i < count; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=423500
#pragma HLS PIPELINE II=1
        stream.write(input[i]);
    }
}
static void write_pixels(hls::stream<Pixel>& stream, Pixel* output, int count) {
#pragma HLS INLINE off
WRITE_PIXELS:
    for (int i = 0; i < count; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=423500
#pragma HLS PIPELINE II=1
        output[i] = stream.read();
    }
}

// Distinct template instances prevent the two dataflow stages sharing state.
template<int ID>
static void morph_stage(hls::stream<Pixel>& input, hls::stream<Pixel>& output,
                        int width, int height, bool dilate, bool enabled) {
#pragma HLS INLINE off
    if (!enabled) {
BYPASS:
        for (int i = 0; i < width * height; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=423500
#pragma HLS PIPELINE II=1
            output.write(input.read());
        }
        return;
    }
    ap_uint<1> previous[MAX_WIDTH], older[MAX_WIDTH];
#pragma HLS BIND_STORAGE variable=previous type=ram_2p impl=bram
#pragma HLS BIND_STORAGE variable=older type=ram_2p impl=bram
    ap_uint<1> window[3][3];
#pragma HLS ARRAY_PARTITION variable=window complete dim=0
    ap_uint<1> top = 0, middle = 0, bottom = 0;
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
                bottom = (y < height) ? ap_uint<1>(input.read() != 0) : previous[x];
                middle = (y == 0) ? bottom : previous[x];
                top = (y <= 1) ? middle : older[x];
                if (y < height) {
                    older[x] = middle;
                    previous[x] = bottom;
                }
            }
            ap_uint<1> column[3] = {top, middle, bottom};
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
                ap_uint<1> any = 0, all = 1;
REDUCE_R:
                for (int r = 0; r < 3; ++r) {
#pragma HLS UNROLL
REDUCE_C:
                    for (int c = 0; c < 3; ++c) {
#pragma HLS UNROLL
                        any |= window[r][c];
                        all &= window[r][c];
                    }
                }
                output.write((dilate ? any : all) ? Pixel(255) : Pixel(0));
            }
        }
    }
}

static void process(const Pixel* input, Pixel* output, int width, int height, int count,
                    bool first_dilate, bool second_dilate, bool second_enabled) {
#pragma HLS INLINE off
#pragma HLS DATAFLOW
    hls::stream<Pixel> raw("raw"), first("first"), second("second");
#pragma HLS STREAM variable=raw depth=64
#pragma HLS STREAM variable=first depth=64
#pragma HLS STREAM variable=second depth=64
    read_pixels(input, raw, count);
    morph_stage<0>(raw, first, width, height, first_dilate, true);
    morph_stage<1>(first, second, width, height, second_dilate, second_enabled);
    write_pixels(second, output, count);
}

void morphology_binary(const Pixel* input, Pixel* output, int width, int height, unsigned int mode) {
#pragma HLS INTERFACE m_axi port=input offset=slave bundle=gmem_in depth=423500
#pragma HLS INTERFACE m_axi port=output offset=slave bundle=gmem_out depth=423500
#pragma HLS INTERFACE s_axilite port=input bundle=control
#pragma HLS INTERFACE s_axilite port=output bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=mode bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if (width < 1 || width > MAX_WIDTH || height < 1 || height > MAX_HEIGHT || mode > CLOSE) return;
    // Prepare scalar control outside the dataflow region.
    const int count = width * height;
    const bool first_dilate = mode == DILATE || mode == CLOSE;
    const bool second_dilate = mode == OPEN;
    const bool second_enabled = mode == OPEN || mode == CLOSE;
    process(input, output, width, height, count, first_dilate, second_dilate, second_enabled);
}
