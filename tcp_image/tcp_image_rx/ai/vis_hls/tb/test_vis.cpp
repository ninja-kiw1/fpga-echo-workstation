// test_vis.cpp -- C simulation of vis_layer against the vectors written by ../gen_vectors.py.
// Each vector = one call: the BRAM image (rest filled with a pseudo-random pattern), the expected return value
// and the expected output words; every other word must be unchanged (no stray writes, a bad descriptor
// writes nothing). Vectors flagged AIV are also compared with the board's ai_view_overlay_rgb (linked in).
//   argv[1] = vectors dir, argv[2] = case list (default cases.txt; cosim uses cases_cosim.txt)
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "vis_layer.hpp"

extern "C" void ai_view_overlay_rgb(const uint8_t *base, const int8_t *logits, int lw, int lh, int thr,
                                    uint8_t *rgb_out, int ow, int oh);

static bool read_all(const std::string& path, std::vector<unsigned char>& buf) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    buf.resize(n);
    bool ok = std::fread(buf.data(), 1, n, f) == (size_t)n;
    std::fclose(f);
    return ok;
}

static word_t mem[VIS_WORDS];
static word_t want[VIS_WORDS];

static word_t from_bytes(const unsigned char* p) {
    word_t w = 0;
    for (int i = 0; i < 16; i++) w.range(8 * i + 7, 8 * i) = p[i];
    return w;
}

static unsigned rnd_state;
static unsigned rnd() {
    rnd_state = rnd_state * 1103515245u + 12345u;
    return rnd_state >> 8;
}

int main(int argc, char** argv) {
    const std::string dir = argc > 1 ? argv[1] : "vectors";
    const std::string list = argc > 2 ? argv[2] : "cases.txt";
    FILE* cl = std::fopen((dir + "/" + list).c_str(), "r");
    if (!cl) {
        std::printf("cannot open %s/%s\n", dir.c_str(), list.c_str());
        return 2;
    }
    char name[256];
    int cases = 0, fails = 0, aiv = 0, words_checked = 0;
    while (std::fscanf(cl, "%255s", name) == 1) {
        std::vector<unsigned char> b;
        if (!read_all(dir + "/" + name + ".bin", b)) {
            std::printf("FAIL %s: cannot read\n", name);
            fails++;
            continue;
        }
        int hd[6];
        std::memcpy(hd, b.data(), sizeof(hd));
        const int desc = hd[1], ret_want = hd[2], nin = hd[3], nexp = hd[4], vflags = hd[5];
        size_t p = sizeof(hd);
        rnd_state = 0x9E3779B9u ^ (unsigned)cases;
        for (int i = 0; i < VIS_WORDS; i++) {
            word_t w;
            for (int j = 0; j < 4; j++) w.range(32 * j + 31, 32 * j) = rnd();
            mem[i] = w;
        }
        for (int c = 0; c < nin; c++) {
            int off, n;
            std::memcpy(&off, b.data() + p, 4);
            std::memcpy(&n, b.data() + p + 4, 4);
            p += 8;
            for (int i = 0; i < n; i++) mem[off + i] = from_bytes(b.data() + p + 16 * i);
            p += 16 * (size_t)n;
        }
        for (int i = 0; i < VIS_WORDS; i++) want[i] = mem[i];
        for (int c = 0; c < nexp; c++) {
            int off, n;
            std::memcpy(&off, b.data() + p, 4);
            std::memcpy(&n, b.data() + p + 4, 4);
            p += 8;
            for (int i = 0; i < n; i++) want[off + i] = from_bytes(b.data() + p + 16 * i);
            p += 16 * (size_t)n;
            words_checked += n;
        }
        const int ret = vis_layer(mem, desc);
        int bad = 0, first = -1;
        for (int i = 0; i < VIS_WORDS; i++) {
            if (mem[i] != want[i]) {
                if (first < 0) first = i;
                bad++;
            }
        }
        bool ok = ret == ret_want && bad == 0;
        if ((vflags & 1) && ok) {
            // the board's own overlay code on the whole frame, compared on this band's rows
            int e[9];
            std::memcpy(e, b.data() + p, sizeof(e));
            p += sizeof(e);
            const int ow = e[0], oh = e[1], lw = e[2], lh = e[3], thr = e[4], ya = e[5], yb = e[6], ooff = e[7];
            const unsigned char* base = e[8] == VIS_BASE_RGB ? b.data() + p : nullptr;
            if (base) p += (size_t)ow * oh * 3;
            const signed char* q = (const signed char*)(b.data() + p);
            std::vector<unsigned char> img((size_t)ow * oh * 3);
            ai_view_overlay_rgb(base, (const int8_t*)q, lw, lh, thr, img.data(), ow, oh);
            const unsigned char* g = img.data() + (size_t)ya * ow * 3;
            const int nbytes = (yb - ya) * ow * 3;
            for (int i = 0; i < nbytes; i++) {
                const unsigned v = mem[ooff + i / 16].range(8 * (i % 16) + 7, 8 * (i % 16));
                if (v != g[i]) {
                    ok = false;
                    std::printf("  %s: ai_view_overlay_rgb differs at byte %d (%u vs %u)\n", name, i, v, g[i]);
                    break;
                }
            }
            aiv++;
        }
        if (!ok) {
            std::printf("FAIL %s: ret %d (want %d), %d words differ (first %d)\n", name, ret, ret_want, bad, first);
            for (int i = 0, shown = 0; i < VIS_WORDS && shown < 12; i++) {
                if (mem[i] != want[i]) {
                    std::printf("  word %d got", i);
                    for (int k = 0; k < 4; k++) std::printf(" %d", (int)(ap_int<32>)mem[i].range(32 * k + 31, 32 * k));
                    std::printf(" want");
                    for (int k = 0; k < 4; k++) std::printf(" %d", (int)(ap_int<32>)want[i].range(32 * k + 31, 32 * k));
                    std::printf("\n");
                    shown++;
                }
            }
            fails++;
        }
        cases++;
    }
    std::fclose(cl);
    std::printf("vis_layer: %d cases, %d failed, %d expected words, %d also vs ai_view_overlay_rgb\n", cases, fails,
                words_checked, aiv);
    if (fails == 0 && cases > 0) std::printf("VIS_TB_PASS\n");
    return fails ? 1 : 0;
}
