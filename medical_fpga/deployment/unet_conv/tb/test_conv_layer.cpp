#include "conv_layer.hpp"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

// Usage: test_conv_layer <vector directory> <case list file name>
// Compares every int16 lane of every output word, including padded channels,
// and checks that nothing is written past the output tensor.

static const uint16_t SENTINEL = 0xA5C3;

template <typename T>
static bool read_file(const std::string& path, std::vector<T>& data) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) return false;
    std::streamsize bytes = f.tellg();
    if (bytes % sizeof(T)) return false;
    data.resize(bytes / sizeof(T));
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

static int run_case(const std::string& dir, const std::string& name, unsigned long& checked) {
    const std::string base = dir + "/" + name + "/";
    std::ifstream cfg((base + "config.txt").c_str());
    int mode, cin, cout, in_h, in_w, relu, shift, qmin, qmax;
    if (!(cfg >> mode >> cin >> cout >> in_h >> in_w >> relu >> shift >> qmin >> qmax)) {
        std::cerr << "Bad config: " << name << "\n";
        return -1;
    }
    std::vector<uint64_t> params_raw;
    std::vector<int16_t> input_raw, expected;
    if (!read_file(base + "params.bin", params_raw) || !read_file(base + "input.bin", input_raw) ||
        !read_file(base + "expected.bin", expected)) {
        std::cerr << "Missing vector files: " << name << "\n";
        return -1;
    }
    const int cig = (cin + LANES - 1) / LANES, cog = (cout + LANES - 1) / LANES;
    const int scale = mode == MODE_TCONV2 ? 2 : 1;
    const size_t in_words = size_t(in_h) * in_w * cig;
    const size_t out_words = size_t(in_h) * scale * in_w * scale * cog;
    if (input_raw.size() != in_words * LANES || expected.size() != out_words * LANES) {
        std::cerr << "Vector size mismatch: " << name << "\n";
        return -1;
    }
    // Buffers are at least the m_axi depth so RTL co-simulation never reads past them.
    std::vector<act_word_t> in(std::max<size_t>(in_words, DEPTH_IN));
    std::vector<act_word_t> out(std::max<size_t>(out_words, DEPTH_OUT) + 64, sentinel_word());
    std::vector<param_word_t> params(std::max<size_t>(params_raw.size(), DEPTH_PARAMS));
    for (size_t n = 0; n < in_words; ++n) in[n] = pack(&input_raw[n * LANES]);
    for (size_t n = 0; n < params_raw.size(); ++n) params[n] = params_raw[n];

    int status = conv_layer(&in[0], &out[0], &params[0], mode, cin, cout, in_h, in_w,
                            relu, shift, qmin, qmax);
    unsigned long mismatches = 0;
    int max_error = 0;
    std::vector<int16_t> actual(out_words * LANES);
    for (size_t n = 0; n < out_words; ++n) {
        for (int i = 0; i < LANES; ++i) {
            const size_t k = n * LANES + i;
            actual[k] = lane(out[n], i);
            const int delta = std::abs(int(actual[k]) - int(expected[k]));
            if (delta) {
                if (mismatches < 5) {
                    const size_t pixel = n / cog;
                    const int out_w = in_w * scale;
                    std::cerr << name << " y=" << pixel / out_w << " x=" << pixel % out_w
                              << " c=" << (n % cog) * LANES + i << " expected=" << expected[k]
                              << " actual=" << actual[k] << "\n";
                }
                ++mismatches;
                max_error = std::max(max_error, delta);
            }
        }
    }
    unsigned long overrun = 0;
    for (size_t n = out_words; n < out.size(); ++n)
        if (out[n] != sentinel_word()) ++overrun;
    if (status != CONV_OK) std::cerr << name << ": status " << status << "\n";
    const bool pass = status == CONV_OK && mismatches == 0 && overrun == 0;
    if (!pass || name.compare(0, 4, "syn_") == 0) {
        std::ofstream f((base + "actual.bin").c_str(), std::ios::binary);
        f.write(reinterpret_cast<const char*>(&actual[0]), actual.size() * sizeof(int16_t));
    }
    checked += out_words * LANES;
    std::cout << (pass ? "PASS " : "FAIL ") << name << " mode=" << mode << " " << cin << "->" << cout
              << " in=" << in_h << "x" << in_w << " values=" << out_words * LANES
              << " mismatches=" << mismatches << " max_error=" << max_error
              << " overrun_words=" << overrun << "\n";
    return pass ? 0 : 1;
}

// Each invalid configuration must return CONV_BAD_CONFIG and write nothing.
static int run_invalid_configs() {
    struct Bad { const char* what; int mode, cin, cout, in_h, in_w, relu, shift, qmin, qmax; };
    const Bad cases[] = {
        {"mode=3", 3, 8, 8, 4, 4, 0, 30, -127, 127},
        {"cin=0", 0, 0, 8, 4, 4, 0, 30, -127, 127},
        {"cout=33", 0, 8, 33, 4, 4, 0, 30, -127, 127},
        {"width=513", 0, 8, 8, 4, 513, 0, 30, -127, 127},
        {"height=0", 1, 8, 8, 0, 4, 0, 30, -127, 127},
        {"tconv width=257", 2, 8, 8, 4, 257, 0, 30, -127, 127},
        {"shift=32", 0, 8, 8, 4, 4, 0, 32, -127, 127},
        {"relu=2", 0, 8, 8, 4, 4, 2, 30, -127, 127},
        {"qmin>qmax", 0, 8, 8, 4, 4, 0, 30, 5, 4},
    };
    std::vector<act_word_t> in(DEPTH_IN), out(DEPTH_OUT, sentinel_word());
    std::vector<param_word_t> params(DEPTH_PARAMS);
    int failures = 0;
    for (const Bad& b : cases) {
        int status = conv_layer(&in[0], &out[0], &params[0], b.mode, b.cin, b.cout, b.in_h,
                                b.in_w, b.relu, b.shift, b.qmin, b.qmax);
        bool untouched = std::all_of(out.begin(), out.end(),
                                     [](const act_word_t& w) { return w == sentinel_word(); });
        bool pass = status == CONV_BAD_CONFIG && untouched;
        std::cout << (pass ? "PASS " : "FAIL ") << "invalid " << b.what << "\n";
        failures += !pass;
    }
    return failures;
}

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "Usage: test_conv_layer <vector dir> <case list>\n";
        return 2;
    }
    const std::string dir = argv[1];
    std::ifstream list((dir + "/" + argv[2]).c_str());
    if (!list) { std::cerr << "Cannot open case list\n"; return 2; }
    int tested = 0, failed = run_invalid_configs();
    unsigned long checked = 0;
    std::string name;
    while (list >> name) {
        int r = run_case(dir, name, checked);
        if (r < 0) return 2;
        failed += r;
        ++tested;
    }
    std::cout << "SUMMARY cases=" << tested << " values=" << checked << " failures=" << failed << "\n";
    return tested > 0 && failed == 0 ? 0 : 1;
}
