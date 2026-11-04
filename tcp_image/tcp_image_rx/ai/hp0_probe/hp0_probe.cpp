// hp0_probe.cpp -- 10-18 HP0 实测：PL 经 S_AXI_HP0_FPD（64 位，厂商 FSBL 把 AFIFM2 配成 64 位）直接读写 DDR。
// 从 src 连续读 n 个 64 位字，算 sum（mod 2^64）和 xor，同时写 dst[i] = src[i] ^ (seed + i)。
// 返回值 = sum；xor 由 xr 输出。PS 用同样公式核对。只在测试比特流里用，不进 f150。
#include <ap_int.h>
typedef ap_uint<64> w64;

w64 hp0_probe(const w64 *src, w64 *dst, unsigned n, w64 seed, w64 *xr) {
#pragma HLS INTERFACE m_axi port=src offset=slave bundle=gmem depth=1048576 max_read_burst_length=64 num_read_outstanding=8
#pragma HLS INTERFACE m_axi port=dst offset=slave bundle=gmem depth=1048576 max_write_burst_length=64 num_write_outstanding=8
#pragma HLS INTERFACE s_axilite port=n
#pragma HLS INTERFACE s_axilite port=seed
#pragma HLS INTERFACE s_axilite port=xr
#pragma HLS INTERFACE s_axilite port=return
    w64 sum = 0, x = 0;
LOOP:
    for (unsigned i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        w64 v = src[i];
        sum += v;
        x ^= v;
        dst[i] = v ^ (seed + i);
    }
    *xr = x;
    return sum;
}
