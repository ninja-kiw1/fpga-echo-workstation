#ifndef CONV_LAYER_HPP
#define CONV_LAYER_HPP
#include <ap_int.h>

// One 128-bit activation word = 8 consecutive int16 channels of one pixel (NHWC).
typedef ap_uint<128> act_word_t;
// Parameter words: 8 packed int8 weights, or one sign-extended int64 value.
typedef ap_uint<64> param_word_t;

static const int LANES = 8;
static const int MAX_C = 32;
static const int MAX_G = MAX_C / LANES;
static const int MAX_W = 512;
static const int MAX_H = 512;
static const int MAX_TAPS = 9;
static const int MAX_SHIFT = 31;

// Largest cosim transfers; must match the m_axi depth pragmas.
static const int DEPTH_IN = 65536;
static const int DEPTH_OUT = 131072;
static const int DEPTH_PARAMS = 2048;

enum ConvMode { MODE_CONV3 = 0, MODE_CONV1 = 1, MODE_TCONV2 = 2 };
enum ConvStatus { CONV_OK = 0, CONV_BAD_CONFIG = 1 };

// Returns CONV_OK, or CONV_BAD_CONFIG without touching memory.
int conv_layer(const act_word_t* in, act_word_t* out, const param_word_t* params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax);
#endif
