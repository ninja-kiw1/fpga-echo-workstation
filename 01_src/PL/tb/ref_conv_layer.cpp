// 参考实现包装：把 PLD 的**原版 conv_layer.cpp 一字不改**地编译进来，
// 只把顶层函数名与内部 static 函数改名，避免和并行版重名。
// conv_layer_orig.inc 是从
//   PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src/conv_layer.cpp
// 原样拷贝的（见 README 里的 md5）。
#define conv_layer conv_layer_ref
#define requantize requantize_ref

#include "conv_layer_orig.inc"

#undef requantize
#undef conv_layer
