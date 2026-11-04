// test_conv_par.cpp —— 并行版 conv 的**逐位等价性**自检（C 仿真）
//
// 做法：同一份输入/参数，分别喂给
//   * conv_layer      —— 并行版（ai/opt_hls/src/conv_layer.cpp，XU 可配）
//   * conv_layer_ref  —— PLD 原版（一字不改地 #include 进来）
// 然后逐 int16 lane 比对，要求 **0 差异**。
//
// 为什么能保证逐位一致：累加是 mod 2^32 的整数加法（有结合律/交换律），
// 并行只是把**互相独立的**像素放到同一拍算，没有改变任何一个像素的加法集合；
// requantize 逐字沿用原实现。所以这个比对是"等价性"的强证据。
//
// 覆盖：20 个真实卷积层的 (mode, cin, cout) 组合（尺寸缩小以加快 C 仿真），
//       另加几个**全尺寸**用例与**奇数宽度**用例（专门打 XU 的右边界保护）。
#include "conv_layer.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

// 并行版由另一个编译单元提供
int conv_layer(const act_word_t* in, act_word_t* out, const param_word_t* params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax);

// 参考实现(PLD 原版)在 ref_conv_layer.cpp 里被改名成 conv_layer_ref
int conv_layer_ref(const act_word_t* in, act_word_t* out, const param_word_t* params,
                   int mode, int cin, int cout, int in_h, int in_w,
                   int relu, int shift, int qmin, int qmax);

struct Case {
    const char* name;
    int mode, cin, cout, in_h, in_w, relu, shift, qmin, qmax;
};

// 20 个真实卷积层配置（cin/cout 取自 network.txt），尺寸按"小而全"取
static const Case CASES[] = {
    {"L1  m0  3->8  512", 0, 3, 8, 32, 32, 1, 27, -32767, 32767},
    {"L2  m0  8->8  512", 0, 8, 8, 32, 32, 1, 30, -127, 127},
    {"L3  m1  3->8  512", 1, 3, 8, 31, 33, 0, 30, -127, 127},   // 奇数宽
    {"L6  m0  8->16 256", 0, 8, 16, 32, 32, 1, 30, -127, 127},
    {"L7  m0 16->16 256", 0, 16, 16, 16, 16, 1, 30, -127, 127},
    {"L8  m1  8->16 256", 1, 8, 16, 16, 17, 0, 30, -127, 127},  // 奇数宽
    {"L11 m0 16->32 128", 0, 16, 32, 16, 16, 1, 30, -127, 127},
    {"L12 m0 32->32 128", 0, 32, 32, 16, 16, 1, 30, -127, 127},
    {"L13 m1 16->32 128", 1, 16, 32, 16, 16, 0, 30, -127, 127},
    {"L15 m2 32->16 tconv", 2, 32, 16, 16, 16, 0, 30, -127, 127},
    {"L16 m0 16->16 256", 0, 16, 16, 16, 16, 1, 30, -127, 127},
    {"L18 m0 32->16 256", 0, 32, 16, 16, 16, 1, 30, -32767, 32767},
    {"L19 m0 16->16 256", 0, 16, 16, 16, 16, 1, 30, -127, 127},
    {"L20 m1 32->16 256", 1, 32, 16, 16, 16, 0, 30, -127, 127},
    {"L22 m2 16->8 tconv", 2, 16, 8, 16, 16, 0, 30, -127, 127},
    {"L23 m0  8->8  512", 0, 8, 8, 16, 16, 1, 30, -127, 127},
    {"L25 m0 16->8  512", 0, 16, 8, 16, 16, 1, 30, -127, 127},
    {"L26 m0  8->8  512", 0, 8, 8, 16, 16, 1, 30, -127, 127},
    {"L27 m1 16->8  512", 1, 16, 8, 16, 16, 0, 30, -127, 127},
    {"L29 m1  8->1  512", 1, 8, 1, 16, 16, 0, 30, -127, 127},
    {"FULL m0 8->8 512", 0, 8, 8, 512, 512, 1, 30, -127, 127},
    {"FULL m1 8->1 512", 1, 8, 1, 512, 512, 0, 30, -127, 127},
    {"FULL m2 32->16 128", 2, 32, 16, 128, 128, 0, 30, -127, 127},
    {"BIG m0 32->32 128", 0, 32, 32, 128, 128, 1, 30, -127, 127},
    {"relu0 m0 16->32", 0, 16, 32, 16, 18, 0, 0, -128, 127},     // shift=0 + 偶宽
};
static const int NCASES = (int)(sizeof(CASES) / sizeof(CASES[0]));

static uint32_t s_seed = 20261006u;

// 确定性伪随机（LCG）：同一序列喂给两个实现
static uint32_t rnd() {
    s_seed = s_seed * 1103515245u + 12345u;
    return (s_seed >> 8) ^ (s_seed << 3);
}

// r1: argv[1] = 只跑前 N 个用例（协同仿真只跑 20 个小尺寸用例，全尺寸的太慢）
int main(int argc, char** argv) {
    const int ncases = (argc > 1 && std::atoi(argv[1]) > 0 && std::atoi(argv[1]) < NCASES) ? std::atoi(argv[1]) : NCASES;
    int failed = 0;
    unsigned long long checked = 0;

    for (int ci = 0; ci < ncases; ++ci) {
        const Case& c = CASES[ci];
        const int cig = (c.cin + LANES - 1) / LANES;
        const int cog = (c.cout + LANES - 1) / LANES;
        const int sc = c.mode == MODE_TCONV2 ? 2 : 1;
        const size_t in_words = (size_t)c.in_h * c.in_w * cig;
        const size_t out_words = (size_t)(c.in_h * sc) * (c.in_w * sc) * cog;

        // 协同仿真的包装层按 pragma 的 depth 整段拷贝（in 65536 / out 131072 字）：缓冲至少开这么大
        std::vector<act_word_t> in(in_words < 65536 ? 65536 : in_words);
        std::vector<param_word_t> par(2048);

        s_seed = 20261006u + ci * 7919u;
        for (size_t n = 0; n < in_words; ++n) {
            act_word_t w = 0;
            for (int i = 0; i < LANES; ++i) {
                // 覆盖满量程 + 小幅值, 让 requantize 的饱和/舍入都被走到
                int16_t v = (int16_t)((int)(rnd() % 40001) - 20000);
                w.range(16 * i + 15, 16 * i) = (uint16_t)v;
            }
            in[n] = w;
        }
        const int weight_words = (c.mode == MODE_CONV3 ? 9 : (c.mode == MODE_CONV1 ? 1 : 4))
                                 * cog * cig * LANES;
        const int cop = cog * LANES;
        for (int n = 0; n < weight_words; ++n) {
            param_word_t w = 0;
            for (int i = 0; i < LANES; ++i) {
                w.range(8 * i + 7, 8 * i) = (uint8_t)((int)(rnd() % 255) - 127);
            }
            par[n] = w;
        }
        for (int k = 0; k < cop; ++k) {
            // bias / mult / offset: 取真实量级（mult 大 -> 打 requantize 的宽位路径）
            par[weight_words + k] = (uint64_t)((int64_t)((int)(rnd() % 2001) - 1000) & 0xFFFFFFFFFFFFULL);
            uint64_t m = (uint64_t)(int64_t)(int32_t)(0x40000000 | (int32_t)(rnd() & 0xFFFF));
            par[weight_words + cop + k] = m;
            par[weight_words + 2 * cop + k] = (uint64_t)((int64_t)(int32_t)rnd()) << 8;
        }

        std::vector<act_word_t> out_a(out_words, (act_word_t)0xDEADBEEFDEADBEEFULL);
        std::vector<act_word_t> out_b(out_words < 131072 ? 131072 : out_words, (act_word_t)0xDEADBEEFDEADBEEFULL);

        int ra = conv_layer_ref(&in[0], &out_a[0], &par[0], c.mode, c.cin, c.cout,
                                c.in_h, c.in_w, c.relu, c.shift, c.qmin, c.qmax);
        int rb = conv_layer(&in[0], &out_b[0], &par[0], c.mode, c.cin, c.cout,
                            c.in_h, c.in_w, c.relu, c.shift, c.qmin, c.qmax);

        size_t bad = 0, first = 0;
        for (size_t n = 0; n < out_words; ++n) {
            if (out_a[n] != out_b[n]) {
                if (bad == 0) first = n;
                ++bad;
            }
        }
        checked += out_words;
        const bool pass = (ra == rb) && (bad == 0);
        if (!pass) ++failed;
        std::printf("%s %-22s mode=%d %2d->%-2d %3dx%-3d words=%-8zu mismatches=%zu%s\n",
                    pass ? "PASS" : "FAIL", c.name, c.mode, c.cin, c.cout, c.in_h, c.in_w,
                    out_words, bad,
                    bad ? (" (first word " + std::to_string(first) + ")").c_str() : "");
    }

    std::printf("\nSUMMARY cases=%d words=%llu failures=%d\n", NCASES, checked, failed);
    return failed == 0 ? 0 : 1;
}
