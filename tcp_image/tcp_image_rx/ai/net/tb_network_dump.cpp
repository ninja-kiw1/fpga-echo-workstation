// P4-2: whole-network C simulation that DUMPS every node's output.
//
// Same layer-table driver as deployment/unet_network/tb/test_network.cpp, but
// instead of comparing against a stored golden it writes each node's tensor to
// <dir>/golden/<name>.bin  (raw int16, h*w*groups*8 little-endian).
//
// Usage: csim.exe <vectors dir>      (the dir holds network.txt, params/, input.bin)
// The two kernels are the UNMODIFIED PLD sources; this file only drives them.
#include <ap_int.h>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

typedef ap_uint<128> act_word_t;
typedef ap_uint<64> param_word_t;
static const int LANES = 8;

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

static int16_t lane(const act_word_t& w, int i) {
    return int16_t(uint16_t(w.range(16 * i + 15, 16 * i).to_uint()));
}

static bool read_tensor(const std::string& path, Tensor& t) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) return false;
    std::streamsize bytes = f.tellg();
    if (bytes != std::streamsize(size_t(t.h) * t.w * t.groups() * LANES) * 2) return false;
    std::vector<int16_t> raw(size_t(t.h) * t.w * t.groups() * LANES);
    f.seekg(0);
    if (!f.read(reinterpret_cast<char*>(&raw[0]), bytes)) return false;
    t.words.resize(raw.size() / LANES);
    for (size_t n = 0; n < t.words.size(); ++n)
        for (int i = 0; i < LANES; ++i) t.words[n].range(16 * i + 15, 16 * i) = uint16_t(raw[n * LANES + i]);
    return true;
}

static bool dump_tensor(const std::string& path, const Tensor& t) {
    std::ofstream f(path.c_str(), std::ios::binary);
    if (!f) return false;
    for (size_t n = 0; n < t.words.size(); ++n)
        for (int i = 0; i < LANES; ++i) {
            const int16_t v = lane(t.words[n], i);
            f.write(reinterpret_cast<const char*>(&v), 2);
        }
    return bool(f);
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
            std::ifstream pf((dir + "/params/" + n.name + ".bin").c_str(), std::ios::binary | std::ios::ate);
            if (!pf) return false;
            std::streamsize bytes = pf.tellg();
            std::vector<uint64_t> raw(bytes / 8);
            pf.seekg(0);
            if (!pf.read(reinterpret_cast<char*>(&raw[0]), bytes)) return false;
            params[n.name].assign(raw.begin(), raw.end());
        } else if (n.kind == "ELT") {
            if (n.mode == 1) n.out_c = n.ca + n.cb;
            if (n.mode == 2) { n.out_h /= 2; n.out_w /= 2; }
        } else if (n.kind != "INPUT") {
            return false;
        }
        nodes.push_back(n);
    }
    for (size_t k = 0; k < nodes.size(); ++k) {
        const Node& c = nodes[k];
        const std::string ins[2] = {c.in0, c.in1};
        for (int j = 0; j < 2; ++j) {
            if (ins[j] == "-") continue;
            if (!by_name.count(ins[j])) { std::cerr << c.name << ": unknown input " << ins[j] << "\n"; return false; }
            const Node& p = *by_name[ins[j]];
            const int want_c = j == 0 ? c.ca : c.cb;
            if (p.out_h != c.h || p.out_w != c.w || p.out_c != want_c) {
                std::cerr << c.name << ": shape mismatch with " << p.name << "\n"; return false;
            }
        }
        by_name[c.name] = &nodes[k];
    }
    return !nodes.empty() && nodes[0].kind == "INPUT";
}

int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "Usage: net_dump <vectors dir>\n"; return 2; }
    const std::string dir = argv[1];
    std::vector<Node> nodes;
    std::map<std::string, std::vector<param_word_t> > params;
    if (!load_network(dir, nodes, params)) { std::cerr << "Cannot load network\n"; return 2; }

    std::map<std::string, Tensor> env;
    int status = 0;
    for (size_t k = 0; k < nodes.size(); ++k) {
        const Node& n = nodes[k];
        Tensor& t = env[n.name];
        t.h = n.out_h; t.w = n.out_w; t.c = n.out_c;
        if (n.kind == "INPUT") {
            if (!read_tensor(dir + "/input.bin", t)) { std::cerr << "bad input\n"; return 2; }
        } else {
            t.words.assign(size_t(t.h) * t.w * t.groups(), act_word_t(0));
            const Tensor& a = env[n.in0];
            if (n.kind == "CONV") {
                status = conv_layer(&a.words[0], &t.words[0], &params[n.name][0], n.mode, n.ca, n.cb,
                                    n.h, n.w, n.relu, n.shift, n.qmin, n.qmax);
            } else {
                const Tensor& b = n.in1 == "-" ? a : env[n.in1];
                status = eltwise_layer(&a.words[0], &b.words[0], &t.words[0], n.mode, n.ca, n.cb,
                                       n.h, n.w, n.m0, n.m1, n.shift, n.qmin, n.qmax);
            }
        }
        if (status != 0) { std::cerr << n.name << ": kernel status " << status << "\n"; return 1; }
        if (!dump_tensor(dir + "/golden/" + n.name + ".bin", t)) {
            std::cerr << "cannot write golden/" << n.name << "\n"; return 2;
        }
        std::cout << "node " << k << " " << n.name << " " << t.h << "x" << t.w << "x" << t.c
                  << " (padded " << t.groups() * LANES << ")\n";
    }
    std::cout << "SUMMARY nodes=" << nodes.size() << " OK" << std::endl;
    return 0;
}
