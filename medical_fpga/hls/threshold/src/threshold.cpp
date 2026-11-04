#include "threshold.hpp"

void threshold_binary(const ap_uint<8>* input, ap_uint<8>* output,
                      int width, int height, ap_uint<8> threshold_value) {
#pragma HLS INTERFACE m_axi port=input offset=slave bundle=gmem_in depth=423500
#pragma HLS INTERFACE m_axi port=output offset=slave bundle=gmem_out depth=423500
#pragma HLS INTERFACE s_axilite port=input bundle=control
#pragma HLS INTERFACE s_axilite port=output bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=threshold_value bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if (width < 1 || width > MAX_WIDTH || height < 1 || height > MAX_HEIGHT) return;
    const int pixels = width * height;
PIXELS:
    for (int i = 0; i < pixels; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=423500
#pragma HLS PIPELINE II=1
        output[i] = input[i] >= threshold_value ? ap_uint<8>(255) : ap_uint<8>(0);
    }
}
