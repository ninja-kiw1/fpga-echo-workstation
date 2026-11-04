#include "eltwise_layer.hpp"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

// Usage: test_eltwise_layer <vector directory> <case list file name>
// Compares every int16 lane of every output word and checks for overruns.

static const uint16_t SENTINEL = 0xA5C3;

static bool read_i16(const std::string& path, std::vector<int16_t>& data) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) return false;
    std::streamsize bytes = f.tellg();
    if (bytes % 2) return false;
    data.resize(bytes / 2);
    f.seekg(0);
    return bytes == 0 || bool(f.read(reinterpret_cast<char*>(&data[0]), bytes));
}

static act_word_t pack(const int16_t* lanes) {
    act_word_t w;
    for (int i = 0; i < LANES; ++i) w.range(16 * i + 15, 16 * i) = uint16_t(lanes[i]);
    return w;
}

static int16_t lane(const act_word_t& w, int i) {
    return int16_t(uint16_t(w.range(16 * i + 15, 16 * i).to_uint()));
}

static act_word_t sentinel_word() {
    int16_t s[LANES];
    std::fill(s, s + LANES, int16_t(SENTINEL));
    return pack(s);
}

static void fill(std::vector<act_word_t>& dst, const std::vector<int16_t>& src) {
    for (size_t n = 0; n < src.size() / LANES; ++n) dst[n] = pack(&src[n * LANES]);
}

static int run_case(const std::string& dir, const std::string& name, unsigned long& checked) {
    const std::string base = dir + "/" + name + "/";
    std::ifstream cfg((base + "config.txt").c_str());
    int mode, ca, cb, h, w, m0, m1, shift, qmin, qmax;
    if (!(cfg >> mode >> ca >> cb >> h >> w >> m0 >> m1 >> shift >> qmin >> qmax)) {
        std::cerr << "Bad config: " << name << "\n";
        return -1;
    }
    std::vector<int16_t> a_raw, b_raw, expected;
    if (!read_i16(base + "a.bin", a_raw) || !read_i16(base + "expected.bin", expected) ||
        (mode != MODE_MAXPOOL && !read_i16(base + "b.bin", b_raw))) {
        std::cerr << "Missing vector files: " << name << "\n";
        return -1;
    }
    const int ga = (ca + LANES - 1) / LANES, gb = (cb + LANES - 1) / LANES;
    const int go = mode == MODE_CONCAT ? ga + gb : ga;
    const int oh = mode == MODE_MAXPOOL ? h / 2 : h, ow = mode == MODE_MAXPOOL ? w / 2 : w;
    const size_t out_words = size_t(oh) * ow * go;
    if (a_raw.size() != size_t(h) * w * ga * LANES || expected.size() != out_words * LANES ||
        (mode != MODE_MAXPOOL && b_raw.size() != size_t(h) * w * gb * LANES)) {
        std::cerr << "Vector size mismatch: " << name << "\n";
        return -1;
    }
    // Buffers are at least the m_axi depth so RTL co-simulation never reads past them.
    std::vector<act_word_t> a(std::max<size_t>(a_raw.size() / LANES, DEPTH_A));
    std::vector<act_word_t> b(std::max<size_t>(b_raw.size() / LANES, DEPTH_B));
    std::vector<act_word_t> out(std::max<size_t>(out_words, DEPTH_OUT) + 64, sentinel_word());
    fill(a, a_raw);
    fill(b, b_raw);

    const int status = eltwise_layer(&a[0], &b[0], &out[0], mode, ca, cb, h, w, m0, m1, shift, qmin, qmax);
    unsigned long mismatches = 0, overrun = 0;
    int max_error = 0;
    std::vector<int16_t> actual(out_words * LANES);
    for (size_t n = 0; n < out_words; ++n) {
        for (int i = 0; i < LANES; ++i) {
            const size_t k = n * LANES + i;
            actual[k] = lane(out[n], i);
            const int delta = std::abs(int(actual[k]) - int(expected[k]));
            if (delta) {
                if (mismatches < 5) {
                    const size_t pixel = n / go;
                    std::cerr << name << " y=" << pixel / ow << " x=" << pixel % ow << " c="
                              << (n % go) * LANES + i << " expected=" << expected[k]
                              << " actual=" << actual[k] << "\n";
                }
                ++mismatches;
                max_error = std::max(max_error, delta);
            }
        }
    }
    for (size_t n = out_words; n < out.size(); ++n)
        if (out[n] != sentinel_word()) ++overrun;
    if (status != ELT_OK) std::cerr << name << ": status " << status << "\n";
    const bool pass = status == ELT_OK && mismatches == 0 && overrun == 0;
    if (!pass || name.compare(0, 4, "syn_") == 0) {
        std::ofstream f((base + "actual.bin").c_str(), std::ios::binary);
        f.write(reinterpret_cast<const char*>(&actual[0]), actual.size() * sizeof(int16_t));
    }
    checked += out_words * LANES;
    std::cout << (pass ? "PASS " : "FAIL ") << name << " mode=" << mode << " ca=" << ca << " cb=" << cb
              << " in=" << h << "x" << w << " values=" << out_words * LANES << " mismatches=" << mismatches
              << " max_error=" << max_error << " overrun_words=" << overrun << "\n";
    return pass ? 0 : 1;
}

// Each invalid configuration must return ELT_BAD_CONFIG and write nothing.
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
    std::vector<act_word_t> a(DEPTH_A), b(DEPTH_B), out(DEPTH_OUT, sentinel_word());
    int failures = 0;
    for (const Bad& c : cases) {
        const int status = eltwise_layer(&a[0], &b[0], &out[0], c.mode, c.ca, c.cb, c.h, c.w,
                                         1, 1, c.shift, c.qmin, c.qmax);
        const bool untouched = std::all_of(out.begin(), out.end(),
                                           [](const act_word_t& w) { return w == sentinel_word(); });
        const bool pass = status == ELT_BAD_CONFIG && untouched;
        std::cout << (pass ? "PASS " : "FAIL ") << "invalid " << c.what << "\n";
        failures += !pass;
    }
    return failures;
}

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "Usage: test_eltwise_layer <vector dir> <case list>\n";
        return 2;
    }
    const std::string dir = argv[1];
    std::ifstream list((dir + "/" + argv[2]).c_str());
    if (!list) { std::cerr << "Cannot open case list\n"; return 2; }
    int tested = 0, failed = run_invalid_configs();
    unsigned long checked = 0;
    std::string name;
    while (list >> name) {
        const int r = run_case(dir, name, checked);
        if (r < 0) return 2;
        failed += r;
        ++tested;
    }
    std::cout << "SUMMARY cases=" << tested << " values=" << checked << " failures=" << failed << "\n";
    return tested > 0 && failed == 0 ? 0 : 1;
}
