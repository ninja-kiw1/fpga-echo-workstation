// test_eltwise_e2.cpp —— E2 与原版逐 int16 lane 比对
//
// 参考实现 = PLD 原版源码一字不改（放进 namespace ref 编进同一单元）。
// 用法: test_eltwise_e2 <向量目录> <用例清单> [随机用例数]
//   A. 原有 26 组向量（含网络 9 个真实节点），对 golden expected.bin 比对；
//   B. 13 种非法配置：必须返回 ELT_BAD_CONFIG 且不写内存；
//   C. PS 驱动（unet_ai.c 单缓冲）实际会发的条带形状：9 个节点 × 多档分辨率；
//   D. 随机差分（含不乒乓的大行 Concat、奇数宽、各种 shift / 饱和），与 ref 比对。
// 每组都检查输出区之后的哨兵（越界写）。
#include "eltwise_layer.hpp"
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace ref {
#include "../../../../../medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp"
}

static const uint16_t SENTINEL = 0xA5C3;
static const int BRAM_WORDS = 16384;  // unet_ai.c: 256 KB 张量 BRAM

static uint64_t rng_state = 0x20261011ULL;
static uint32_t rnd() {
    rng_state ^= rng_state << 13; rng_state ^= rng_state >> 7; rng_state ^= rng_state << 17;
    return uint32_t(rng_state >> 11);
}
static int rnd_range(int lo, int hi) { return lo + int(rnd() % uint32_t(hi - lo + 1)); }

static act_word_t pack(const int16_t* lanes) {
    act_word_t w;
    for (int i = 0; i < LANES; ++i) w.range(16 * i + 15, 16 * i) = uint16_t(lanes[i]);
    return w;
}
static int16_t lane_of(const act_word_t& w, int i) {
    return int16_t(uint16_t(w.range(16 * i + 15, 16 * i).to_uint()));
}
static act_word_t sentinel_word() {
    int16_t s[LANES];
    std::fill(s, s + LANES, int16_t(SENTINEL));
    return pack(s);
}

static bool read_i16(const std::string& path, std::vector<int16_t>& data) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) return false;
    std::streamsize bytes = f.tellg();
    if (bytes % 2) return false;
    data.resize(bytes / 2);
    f.seekg(0);
    return bytes == 0 || bool(f.read(reinterpret_cast<char*>(&data[0]), bytes));
}

struct Cfg { int mode, ca, cb, h, w, m0, m1, shift, qmin, qmax; };

static size_t out_words_of(const Cfg& c) {
    const int ga = (c.ca + LANES - 1) / LANES, gb = (c.cb + LANES - 1) / LANES;
    const int go = c.mode == MODE_CONCAT ? ga + gb : ga;
    const int oh = c.mode == MODE_MAXPOOL ? c.h / 2 : c.h, ow = c.mode == MODE_MAXPOOL ? c.w / 2 : c.w;
    return size_t(oh) * ow * go;
}

// 输出区按最大的真实节点开：cat_1 整图 512×512×2 字 = 2·DEPTH_OUT；后面 64 字是哨兵。
static std::vector<act_word_t> g_a(DEPTH_A), g_b(DEPTH_B), g_out(2 * DEPTH_OUT + 64), g_ref(2 * DEPTH_OUT + 64);
static unsigned long g_values = 0;

// 跑 E2（和可选的 ref），返回不一致的 lane 数；越界或状态不对计入 bad。
static int run_one(const std::string& name, const Cfg& c, const std::vector<int16_t>* expected,
                   bool with_ref, bool quiet) {
    const size_t ow = out_words_of(c);
    std::fill(g_out.begin(), g_out.end(), sentinel_word());
    const int st = eltwise_layer(&g_a[0], &g_b[0], &g_out[0], c.mode, c.ca, c.cb, c.h, c.w,
                                 c.m0, c.m1, c.shift, c.qmin, c.qmax);
    unsigned long bad = 0, overrun = 0;
    if (with_ref) {
        std::fill(g_ref.begin(), g_ref.end(), sentinel_word());
        const int rs = ref::eltwise_layer(&g_a[0], &g_b[0], &g_ref[0], c.mode, c.ca, c.cb, c.h, c.w,
                                          c.m0, c.m1, c.shift, c.qmin, c.qmax);
        if (rs != st) { std::cerr << name << ": status " << st << " ref " << rs << "\n"; ++bad; }
        for (size_t n = 0; n < ow; ++n)
            for (int i = 0; i < LANES; ++i)
                if (lane_of(g_out[n], i) != lane_of(g_ref[n], i)) {
                    if (bad < 3) std::cerr << name << " word " << n << " lane " << i << " e2="
                                           << lane_of(g_out[n], i) << " ref=" << lane_of(g_ref[n], i) << "\n";
                    ++bad;
                }
    }
    if (expected) {
        for (size_t n = 0; n < ow; ++n)
            for (int i = 0; i < LANES; ++i)
                if (lane_of(g_out[n], i) != (*expected)[n * LANES + i]) {
                    if (bad < 3) std::cerr << name << " word " << n << " lane " << i << " e2="
                                           << lane_of(g_out[n], i) << " golden=" << (*expected)[n * LANES + i] << "\n";
                    ++bad;
                }
    }
    for (size_t n = ow; n < g_out.size(); ++n)
        if (g_out[n] != sentinel_word()) ++overrun;
    if (st != ELT_OK) ++bad;
    g_values += ow * LANES;
    const bool pass = bad == 0 && overrun == 0;
    if (!pass || !quiet)
        std::cout << (pass ? "PASS " : "FAIL ") << name << " mode=" << c.mode << " ca=" << c.ca << " cb=" << c.cb
                  << " in=" << c.h << "x" << c.w << " values=" << ow * LANES << " bad=" << bad
                  << " overrun_words=" << overrun << "\n";
    return pass ? 0 : 1;
}

// ---------------- A. 原有向量 ----------------
static int run_vector_case(const std::string& dir, const std::string& name) {
    const std::string base = dir + "/" + name + "/";
    std::ifstream cfg((base + "config.txt").c_str());
    Cfg c;
    if (!(cfg >> c.mode >> c.ca >> c.cb >> c.h >> c.w >> c.m0 >> c.m1 >> c.shift >> c.qmin >> c.qmax)) {
        std::cerr << "Bad config: " << name << "\n";
        return -1;
    }
    std::vector<int16_t> a_raw, b_raw, expected;
    if (!read_i16(base + "a.bin", a_raw) || !read_i16(base + "expected.bin", expected) ||
        (c.mode != MODE_MAXPOOL && !read_i16(base + "b.bin", b_raw))) {
        std::cerr << "Missing vector files: " << name << "\n";
        return -1;
    }
    if (expected.size() != out_words_of(c) * LANES) { std::cerr << "size mismatch " << name << "\n"; return -1; }
    std::fill(g_a.begin(), g_a.end(), act_word_t(0));
    std::fill(g_b.begin(), g_b.end(), act_word_t(0));
    for (size_t n = 0; n < a_raw.size() / LANES; ++n) g_a[n] = pack(&a_raw[n * LANES]);
    for (size_t n = 0; n < b_raw.size() / LANES; ++n) g_b[n] = pack(&b_raw[n * LANES]);
    return run_one("vec " + name, c, &expected, true, false);
}

// ---------------- B. 非法配置 ----------------
static int run_invalid_configs() {
    struct Bad { const char* what; int mode, ca, cb, h, w, shift, qmin, qmax; };
    const Bad cases[] = {
        {"mode=3", 3, 8, 8, 4, 4, 30, -127, 127},
        {"ca=0", 0, 0, 0, 4, 4, 30, -127, 127},
        {"ca=33", 2, 33, 0, 4, 4, 0, 0, 0},
        {"width=513", 0, 8, 8, 4, 513, 30, -127, 127},
        {"height=0", 0, 8, 8, 0, 4, 30, -127, 127},
        {"add ca!=cb", 0, 8, 16, 4, 4, 30, -127, 127},
        {"concat ca=5", 1, 5, 8, 4, 4, 30, -127, 127},
        {"concat cb=0", 1, 8, 0, 4, 4, 30, -127, 127},
        {"concat 24+16", 1, 24, 16, 4, 4, 30, -127, 127},
        {"pool odd height", 2, 8, 0, 3, 4, 0, 0, 0},
        {"pool odd width", 2, 8, 0, 4, 5, 0, 0, 0},
        {"shift=32", 0, 8, 8, 4, 4, 32, -127, 127},
        {"qmin>qmax", 1, 8, 8, 4, 4, 30, 5, 4},
    };
    int failures = 0;
    for (const Bad& c : cases) {
        std::fill(g_out.begin(), g_out.end(), sentinel_word());
        const int status = eltwise_layer(&g_a[0], &g_b[0], &g_out[0], c.mode, c.ca, c.cb, c.h, c.w,
                                         1, 1, c.shift, c.qmin, c.qmax);
        const bool untouched = std::all_of(g_out.begin(), g_out.end(),
                                           [](const act_word_t& w) { return w == sentinel_word(); });
        const bool pass = status == ELT_BAD_CONFIG && untouched;
        std::cout << (pass ? "PASS " : "FAIL ") << "invalid " << c.what << "\n";
        failures += !pass;
    }
    return failures;
}

static void fill_random(size_t na, size_t nb, int profile) {
    for (size_t n = 0; n < na + nb; ++n) {
        int16_t l[LANES];
        for (int i = 0; i < LANES; ++i) {
            switch (profile) {
            case 0: l[i] = int16_t(rnd_range(-127, 127)); break;                 // 网络里的 INT8 激活
            case 1: l[i] = int16_t(rnd_range(-32768, 32767)); break;             // 满 int16
            default: { const int e[] = {-32768, -32767, -1, 0, 1, 32766, 32767};
                       l[i] = int16_t(e[rnd() % 7]); }                          // 极值
            }
        }
        if (n < na) g_a[n] = pack(l); else g_b[n - na] = pack(l);
    }
}

// ---------------- C. PS 驱动实际的条带形状 ----------------
// network.txt 的 9 个 ELT 节点（输入尺寸按 512 档；其它档等比缩小）。
struct Node { const char* name; int mode, ca, cb, div; int m0, m1, shift, qmin, qmax; };
static const Node NODES[] = {
    {"add", 0, 8, 8, 1, 1056516189, 437131304, 30, -127, 127},
    {"down_path_0_pool", 2, 8, 0, 1, 0, 0, 0, -127, 127},
    {"add_1", 0, 16, 16, 2, 1011244993, 516164220, 30, -127, 127},
    {"down_path_1_pool", 2, 16, 0, 2, 0, 0, 0, -127, 127},
    {"add_2", 0, 32, 32, 4, 1133319055, 283347098, 30, -127, 127},
    {"cat", 1, 16, 16, 2, 737236793, 1073741824, 30, -127, 127},
    {"add_3", 0, 16, 16, 2, 1042775647, 350889203, 30, -127, 127},
    {"cat_1", 1, 8, 8, 1, 1057911908, 1073741824, 30, -127, 127},
    {"add_4", 0, 8, 8, 1, 1081008032, 1015711181, 30, -127, 127},
};

// max_area > 0 时只跑面积不超过它的档位（RTL 协同仿真用，整图 512 太慢）。
static int run_strip_shapes(long max_area) {
    // 档位：整图 512/256/128/64/32 与 ROI 尺寸（unet_set_size，宽高 4 对齐）。
    const int sizes[][2] = {{512, 512}, {256, 256}, {128, 128}, {64, 64}, {32, 32},
                            {140, 152}, {96, 200}, {44, 36}, {512, 4}, {4, 512}};
    int failures = 0, cases = 0;
    for (const auto& sz : sizes) {
        if (max_area > 0 && long(sz[0]) * sz[1] > max_area) continue;
        for (const Node& nd : NODES) {
            const int H = sz[0] / nd.div, W = sz[1] / nd.div;
            if (H < 2 || W < 2) continue;
            const int ga = (nd.ca + 7) / 8, gb = (nd.cb + 7) / 8;
            const int rin_a = W * ga, rin_b = (nd.mode == 2) ? 0 : W * gb;
            const int go = nd.mode == 1 ? ga + gb : ga;
            const int rout = (nd.mode == 2 ? W / 2 : W) * go;
            const int OH = nd.mode == 2 ? H / 2 : H;
            int S = nd.mode == 2 ? BRAM_WORDS / (2 * rin_a + rout) : BRAM_WORDS / (rin_a + rin_b + rout);
            if (S > OH) S = OH;
            // 一个满条带 + 末尾那条（可能更矮）。
            const int heights[] = {S, OH % S ? OH % S : S};
            for (int hi = 0; hi < 2; ++hi) {
                const int Sact = heights[hi];
                const int nin = nd.mode == 2 ? 2 * Sact : Sact;
                fill_random(size_t(nin) * rin_a, size_t(nin) * rin_b, cases % 3);
                Cfg c = {nd.mode, nd.ca, nd.cb, nin, W, nd.m0, nd.m1, nd.shift, nd.qmin, nd.qmax};
                const std::string name = std::string("strip ") + nd.name + " " + std::to_string(sz[0]) + "x" +
                                         std::to_string(sz[1]) + " S=" + std::to_string(Sact);
                failures += run_one(name, c, nullptr, true, true);
                ++cases;
            }
        }
    }
    std::cout << "STRIPS cases=" << cases << " failures=" << failures << "\n";
    return failures;
}

// ---------------- D. 随机差分 ----------------
static int run_random(int count) {
    int failures = 0, pp_off = 0;
    for (int t = 0; t < count; ++t) {
        Cfg c;
        c.mode = rnd_range(0, 2);
        if (c.mode == MODE_CONCAT) {
            c.ca = 8 * rnd_range(1, 3);
            c.cb = rnd_range(1, 32 - c.ca);
        } else {
            c.ca = rnd_range(1, 32);
            c.cb = c.mode == MODE_ADD ? c.ca : 0;
        }
        const int ga = (c.ca + 7) / 8, gb = (c.cb + 7) / 8;
        const int big = rnd_range(0, 3) == 0;
        c.w = big ? rnd_range(256, 512) : rnd_range(1, 96);
        if (c.mode == MODE_MAXPOOL) c.w = std::max(2, c.w & ~1);
        const int per_row = c.w * (c.mode == MODE_CONCAT ? ga + gb : ga) + c.w * ga +
                            (c.mode == MODE_ADD ? c.w * gb : 0);
        int hmax = std::max(1, std::min(MAX_H, int(DEPTH_A / (c.w * std::max(ga, gb)))));
        hmax = std::min(hmax, std::max(1, 6 * BRAM_WORDS / per_row));
        c.h = rnd_range(1, std::min(hmax, 40));
        if (c.mode == MODE_MAXPOOL) c.h = std::max(2, c.h & ~1);
        c.shift = rnd_range(0, 31);
        c.qmin = rnd_range(-32768, 0);
        c.qmax = rnd_range(c.qmin, 32767);
        c.m0 = int(rnd()); c.m1 = int(rnd());
        if (rnd_range(0, 2) == 0) { c.qmin = -127; c.qmax = 127; c.shift = 30; }
        if (c.mode == MODE_CONCAT && (c.w * ga > 1024 || c.w * gb > 1024)) ++pp_off;
        fill_random(size_t(c.h) * c.w * ga, c.mode == MODE_MAXPOOL ? 0 : size_t(c.h) * c.w * gb, t % 3);
        failures += run_one("random " + std::to_string(t), c, nullptr, true, true);
    }
    // 不乒乓的路径（行 > 半个缓冲）必须专门覆盖到。
    const Cfg forced[] = {{1, 24, 8, 3, 512, 12345, -999, 7, -500, 600},
                          {1, 8, 24, 2, 500, -7, 1 << 30, 30, -32768, 32767},
                          {1, 16, 16, 1, 512, 1, 1, 0, -32768, 32767},
                          {2, 32, 0, 4, 512, 0, 0, 0, 0, 0},
                          {2, 25, 0, 2, 2, 0, 0, 0, 0, 0}};
    for (int t = 0; t < 5; ++t) {
        const Cfg& c = forced[t];
        const int ga = (c.ca + 7) / 8, gb = (c.cb + 7) / 8;
        fill_random(size_t(c.h) * c.w * ga, c.mode == MODE_MAXPOOL ? 0 : size_t(c.h) * c.w * gb, 1);
        failures += run_one("forced " + std::to_string(t), c, nullptr, true, false);
    }
    std::cout << "RANDOM cases=" << count << " (+5 forced, concat non-pingpong " << pp_off
              << " random) failures=" << failures << "\n";
    return failures;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: test_eltwise_e2 <vector dir> <case list> [random cases] [strip max area]\n";
        return 2;
    }
    std::cout.setf(std::ios::unitbuf);  // 崩溃时也不丢已经输出的行
    std::cerr << "test_eltwise_e2 start\n";
    const std::string dir = argv[1];
    const int nrand = argc > 3 ? std::atoi(argv[3]) : 300;
    const long strip_area = argc > 4 ? std::atol(argv[4]) : 0;
    std::ifstream list((dir + "/" + argv[2]).c_str());
    if (!list) { std::cerr << "Cannot open case list\n"; return 2; }
    int failed = run_invalid_configs(), tested = 0;
    std::string name;
    while (list >> name) {
        const int r = run_vector_case(dir, name);
        if (r < 0) return 2;
        failed += r;
        ++tested;
    }
    failed += run_strip_shapes(strip_area);
    failed += run_random(nrand);
    std::cout << "SUMMARY vector_cases=" << tested << " values=" << g_values << " failures=" << failed << "\n";
    return tested > 0 && failed == 0 ? 0 : 1;
}
