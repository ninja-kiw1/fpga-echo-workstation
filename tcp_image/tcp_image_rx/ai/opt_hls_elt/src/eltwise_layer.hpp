#ifndef ELTWISE_LAYER_HPP
#define ELTWISE_LAYER_HPP
#include <ap_int.h>

// Same activation layout as unet_conv: NHWC, int16 containers,
// one 128-bit word = 8 consecutive channels of one pixel.
typedef ap_uint<128> act_word_t;

static const int LANES = 8;
static const int MAX_C = 32;
static const int MAX_G = MAX_C / LANES;
static const int MAX_W = 512;
static const int MAX_H = 512;
static const int MAX_SHIFT = 31;

// Largest cosim transfers; must match the m_axi depth pragmas.
static const int DEPTH_A = 262144;
static const int DEPTH_B = 262144;
static const int DEPTH_OUT = 262144;

enum EltwiseMode { MODE_ADD = 0, MODE_CONCAT = 1, MODE_MAXPOOL = 2 };
enum EltwiseStatus { ELT_OK = 0, ELT_BAD_CONFIG = 1 };

// MODE_ADD     out = clamp(RNE(a*m0 + b*m1 >> shift)); ca == cb channels.
// MODE_CONCAT  out = [clamp(RNE(a*m0 >> shift)), clamp(RNE(b*m1 >> shift))];
//              ca must be a multiple of 8, ca + cb <= 32.
// MODE_MAXPOOL 2x2 stride 2 max of a (ca channels); h, w even. in_b, cb,
//              m0, m1, shift, qmin, qmax are ignored.
// h, w are the input size. Returns ELT_BAD_CONFIG without touching memory.
int eltwise_layer(const act_word_t* in_a, const act_word_t* in_b, act_word_t* out,
                  int mode, int ca, int cb, int h, int w,
                  int m0, int m1, int shift, int qmin, int qmax);
#endif
