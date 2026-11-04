// Whole-network C simulation: runs the 30-node layer table through the two
// HLS kernels exactly as the ARM program will, and compares bit-exactly.
//
// Usage: test_network <vector dir> <image list file name>
//   every image: final output vs lwnet output_quantized (all padded lanes)
//   images with golden/: every intermediate node as well
// Writes images/<img>/final_actual.bin: channel 0 as raw int8, 512x512.
#include <ap_int.h>
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

typedef ap_uint<128> act_word_t;
typedef ap_uint<64> param_word_t;
static const int LANES = 8;

// Declared here because both kernel headers define the same constants.
// C++ name mangling makes any signature drift a link error.
int conv_layer(const act_word_t* in, act_word_t* out, const param_word_t* params,
               int mode, int cin, int cout, int in_h, int in_w,
               int relu, int shift, int qmin, int qmax);
int eltwise_layer(const act_word_t* in_a, const act_word_t* in_b, act_word_t* out,
                  int mode, int ca, int cb, int h, int w,
                  int m0, int m1, int shift, int qmin, int qmax);

struct Node {
    std::string name, kind, in0, in1;
    int mode, ca, cb, h, w, relu, shift, qmin, qmax, m0, m1;
    int out_h, out_w, out_c;
};

struct Tensor {
    int h = 0, w = 0, c = 0;
    std::vector<act_word_t> words;
    int groups() const { return (c + LANES - 1) / LANES; }
};

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

static int16_t lane(const act_word_t& w, int i) {
    return int16_t(uint16_t(w.range(16 * i + 15, 16 * i).to_uint()));
}

static bool read_tensor(const std::string& path, Tensor& t) {
    std::vector<int16_t> raw;
    if (!read_file(path, raw) || raw.size() != size_t(t.h) * t.w * t.groups() * LANES) return false;
    t.words.resize(raw.size() / LANES);
    for (size_t n = 0; n < t.words.size(); ++n)
        for (int i = 0; i < LANES; ++i) t.words[n].range(16 * i + 15, 16 * i) = uint16_t(raw[n * LANES + i]);
    return true;
}

// Counts differing int16 lanes; prints the first few.
static unsigned long compare(const std::string& what, const Tensor& actual, const Tensor& expected) {
    unsigned long bad = 0;
    for (size_t n = 0; n < expected.words.size(); ++n) {
        if (actual.words[n] == expected.words[n]) continue;
        for (int i = 0; i < LANES; ++i) {
            if (lane(actual.words[n], i) == lane(expected.words[n], i)) continue;
            if (bad < 3) {
                const size_t pixel = n / expected.groups();
                std::cerr << what << " y=" << pixel / expected.w << " x=" << pixel % expected.w
                          << " c=" << (n % expected.groups()) * LANES + i
                          << " expected=" << lane(expected.words[n], i)
                          << " actual=" << lane(actual.words[n], i) << "\n";
            }
            ++bad;
        }
    }
    return bad;
}

static bool load_network(const std::string& dir, std::vector<Node>& nodes,
                         std::map<std::string, std::vector<param_word_t> >& params) {
    std::ifstream f((dir + "/network.txt").c_str());
    if (!f) return false;
    Node n;
    std::map<std::string, const Node*> by_name;
    while (f >> n.name >> n.kind >> n.in0 >> n.in1 >> n.mode >> n.ca >> n.cb >> n.h >> n.w >> n.relu >>
           n.shift >> n.qmin >> n.qmax >> n.m0 >> n.m1) {
        n.out_h = n.h; n.out_w = n.w; n.out_c = n.ca;
        if (n.kind == "CONV") {
            n.out_c = n.cb;
            if (n.mode == 2) { n.out_h *= 2; n.out_w *= 2; }
            std::vector<uint64_t> raw;
            if (!read_file(dir + "/params/" + n.name + ".bin", raw)) return false;
            params[n.name].assign(raw.begin(), raw.end());
        } else if (n.kind == "ELT") {
            if (n.mode == 1) n.out_c = n.ca + n.cb;
            if (n.mode == 2) { n.out_h /= 2; n.out_w /= 2; }
        } else if (n.kind != "INPUT") {
            return false;
        }
        nodes.push_back(n);
    }
    // Inputs must precede their consumers and agree on shape.
    for (size_t k = 0; k < nodes.size(); ++k) {
        const Node& c = nodes[k];
        const std::string ins[2] = {c.in0, c.in1};
        for (int j = 0; j < 2; ++j) {
            if (ins[j] == "-") continue;
            if (!by_name.count(ins[j])) { std::cerr << c.name << ": unknown input " << ins[j] << "\n"; return false; }
            const Node& p = *by_name[ins[j]];
            const int want_c = j == 0 ? c.ca : c.cb;
            if (p.out_h != c.h || p.out_w != c.w || p.out_c != want_c) {
                std::cerr << c.name << ": shape mismatch with " << p.name << "\n";
                return false;
            }
        }
        by_name[c.name] = &nodes[k];
    }
    return !nodes.empty() && nodes[0].kind == "INPUT";
}

static int run_image(const std::string& dir, const std::string& image, const std::vector<Node>& nodes,
                     std::map<std::string, std::vector<param_word_t> >& params) {
    const std::string base = dir + "/images/" + image + "/";
    std::ifstream probe((base + "golden/" + nodes.back().name + ".bin").c_str());
    const bool per_node = bool(probe);
    std::map<std::string, Tensor> env;
    int failures = 0;
    for (size_t k = 0; k < nodes.size(); ++k) {
        const Node& n = nodes[k];
        Tensor& t = env[n.name];
        t.h = n.out_h; t.w = n.out_w; t.c = n.out_c;
        int status = 0;
        if (n.kind == "INPUT") {
            if (!read_tensor(base + "input.bin", t)) { std::cerr << image << ": bad input\n"; return -1; }
        } else {
            t.words.assign(size_t(t.h) * t.w * t.groups(), act_word_t(0));
            const Tensor& a = env[n.in0];
            if (n.kind == "CONV") {
                status = conv_layer(&a.words[0], &t.words[0], &params[n.name][0], n.mode, n.ca, n.cb,
                                    n.h, n.w, n.relu, n.shift, n.qmin, n.qmax);
            } else {
                // MaxPool ignores in_b; pass a valid buffer anyway.
                const Tensor& b = n.in1 == "-" ? a : env[n.in1];
                status = eltwise_layer(&a.words[0], &b.words[0], &t.words[0], n.mode, n.ca, n.cb,
                                       n.h, n.w, n.m0, n.m1, n.shift, n.qmin, n.qmax);
            }
        }
        if (status != 0) {
            std::cout << "FAIL " << image << " " << n.name << " status=" << status << "\n";
            ++failures;
            continue;
        }
        if (per_node) {
            Tensor g;
            g.h = t.h; g.w = t.w; g.c = t.c;
            if (!read_tensor(base + "golden/" + n.name + ".bin", g)) { std::cerr << "bad golden " << n.name << "\n"; return -1; }
            const unsigned long bad = compare(image + "/" + n.name, t, g);
            std::cout << (bad ? "FAIL " : "PASS ") << image << " node " << n.name << " " << t.h << "x" << t.w
                      << "x" << t.c << " mismatches=" << bad << "\n";
            failures += bad != 0;
        }
    }
    const Tensor& out = env[nodes.back().name];
    Tensor expected;
    expected.h = out.h; expected.w = out.w; expected.c = out.c;
    if (!read_tensor(base + "expected_final.bin", expected)) { std::cerr << image << ": bad expected\n"; return -1; }
    const unsigned long bad = compare(image + "/final", out, expected);
    std::ofstream f((base + "final_actual.bin").c_str(), std::ios::binary);
    for (size_t p = 0; p < out.words.size() / out.groups(); ++p) {
        const int8_t v = int8_t(lane(out.words[p * out.groups()], 0));
        f.write(reinterpret_cast<const char*>(&v), 1);
    }
    failures += bad != 0;
    std::cout << (bad == 0 && failures == 0 ? "PASS " : "FAIL ") << "image " << image << " final "
              << out.h << "x" << out.w << " mismatches=" << bad << (per_node ? " (all nodes checked)" : "") << "\n";
    return failures;
}

int main(int argc, char** argv) {
    if (argc != 3) { std::cerr << "Usage: test_network <vector dir> <image list>\n"; return 2; }
    const std::string dir = argv[1];
    std::vector<Node> nodes;
    std::map<std::string, std::vector<param_word_t> > params;
    if (!load_network(dir, nodes, params)) { std::cerr << "Cannot load network\n"; return 2; }
    std::ifstream list((dir + "/" + argv[2]).c_str());
    if (!list) { std::cerr << "Cannot open image list\n"; return 2; }
    std::string image;
    int images = 0, failed = 0;
    while (list >> image) {
        const int r = run_image(dir, image, nodes, params);
        if (r < 0) return 2;
        failed += r != 0;
        ++images;
    }
    std::cout << "SUMMARY nodes=" << nodes.size() << " images=" << images << " failed_images=" << failed << "\n";
    return images > 0 && failed == 0 ? 0 : 1;
}
