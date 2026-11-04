#ifndef CONV_LAYER_STREAM_HPP
#define CONV_LAYER_STREAM_HPP
#include <ap_int.h>
#include <hls_stream.h>

// 流接口版：把 PLD 原版 conv_layer 的 m_axi 出入口换成 AXI-Stream，
// 计算核心（行缓存 / 8x8 MAC / 重缩放 / 舍入 / 饱和）**一行没改**。
// 这样 PS 可以经寄存器流把张量推进来、把结果拉回去（本板没有 PL→PS，不能读 DDR）。

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

enum ConvMode { MODE_CONV3 = 0, MODE_CONV1 = 1, MODE_TCONV2 = 2 };
enum ConvStatus { CONV_OK = 0, CONV_BAD_CONFIG = 1 };

// 参数流顺序（与原版内存布局一致）：
//   [weights: taps*cog*cig*LANES 个字][bias: cop][mult: cop][offset: cop]
//   其中 cop = cog*LANES = ceil(cout/8)*8
// 激活流顺序：输入 H*W*cig 个 act_word（NHWC，每像素 cig 个字）；
//             输出 out_h*out_w*cog 个 act_word。
int conv_layer(hls::stream<act_word_t>& in, hls::stream<act_word_t>& out,
               hls::stream<param_word_t>& params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax);
#endif
