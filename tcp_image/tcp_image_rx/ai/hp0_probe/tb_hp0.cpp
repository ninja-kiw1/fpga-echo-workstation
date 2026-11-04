#include <ap_int.h>
#include <cstdio>
#include <vector>
typedef ap_uint<64> w64;
w64 hp0_probe(const w64 *src, w64 *dst, unsigned n, w64 seed, w64 *xr);
int main() {
    const unsigned n = 4099;
    // 协同仿真包装层按 pragma 的 depth（1048576 字）整段拷贝：缓冲至少开这么大
    std::vector<w64> s(1u << 20), d(1u << 20);
    w64 sum = 0, x = 0, seed = 0x0123456789ABCDEFULL;
    for (unsigned i = 0; i < n; i++) { s[i] = (w64)(0x9E3779B97F4A7C15ULL * (i + 1)); sum += s[i]; x ^= s[i]; }
    w64 xr = 0;
    w64 r = hp0_probe(s.data(), d.data(), n, seed, &xr);
    int bad = (r != sum) + (xr != x);
    for (unsigned i = 0; i < n; i++) bad += d[i] != (s[i] ^ (seed + i));
    printf("%s bad=%d\n", bad ? "HP0_TB_FAIL" : "HP0_TB_PASS", bad);
    return bad != 0;
}
