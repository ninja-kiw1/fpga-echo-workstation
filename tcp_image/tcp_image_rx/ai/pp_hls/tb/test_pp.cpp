// test_pp.cpp -- C simulation of pp_layer against the vectors written by ../gen_vectors.py
// (goldens: scipy / echo_ws.clean / ct_volume.upsample_bilinear / the board's ai_view_count, which is
// also linked here and re-checked on every 4-connected whole-frame count case).
// Every case runs as one call; cases a band call can handle (k = 1, no CLEAN) also run as 3 uneven bands,
// each with an input buffer holding only that band's rows (in_row0), and must give the same results.
//   argv[1] = vectors dir, argv[2] = case list (default cases.txt; cosim uses cases_cosim.txt)
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "pp_layer.hpp"

extern "C" uint32_t ai_view_count(const int8_t *logits, int lw, int lh, int thr, uint32_t *components);

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

static word_t in[PP_DEPTH_IN];
static word_t out[PP_DEPTH_OUT];

// pack frame rows [r0, r1) of q (h x w int8) into `in`, 16 logits per word
static void pack_rows(const signed char* q, int w, int r0, int r1) {
    const int stride = (w + 15) / 16;
    for (int y = r0; y < r1; y++)
        for (int j = 0; j < stride; j++) {
            word_t wd = 0;
            for (int i = 0; i < 16; i++) {
                const int x = 16 * j + i;
                if (x < w) wd.range(8 * i + 7, 8 * i) = (ap_uint<8>)(unsigned char)q[y * w + x];
            }
            in[(y - r0) * stride + j] = wd;
        }
}

static void scrub(int n) {
    for (int i = 0; i < n; i++) out[i] = ~word_t(0);      // stale data
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
    int cases = 0, fails = 0, aiv = 0, banded = 0;
    while (std::fscanf(cl, "%255s", name) == 1) {
        std::vector<unsigned char> b;
        if (!read_all(dir + "/" + name + ".bin", b)) {
            std::printf("FAIL %s: cannot read\n", name);
            fails++;
            continue;
        }
        int hd[16], tiles[16];
        std::memcpy(hd, b.data(), 64);
        std::memcpy(tiles, b.data() + 64, 64);
        const int op = hd[1], h = hd[2], w = hd[3], x0 = hd[4], y0 = hd[5], cw = hd[6], ch = hd[7], k = hd[8];
        const int thr = hd[9], tn = hd[10], ecount = hd[11], ecomps = hd[12], elargest = hd[13];
        const int mh = hd[14], mw = hd[15];
        const signed char* q = (const signed char*)(b.data() + 128);
        const unsigned char* em = b.data() + 128 + h * w;
        const int words = (mw + 127) / 128;
        std::vector<unsigned char> got(mh * mw);

        // --- one call ---
        pack_rows(q, w, 0, h);
        scrub(8 + mh * words);
        const int st = pp_layer(in, out, op | PP_FIRST | PP_LAST, h, w, x0, y0, cw, ch, k, thr, tn, 0, mh, 0);
        word_t res = out[0], tw[4];
        for (int i = 0; i < 4; i++) tw[i] = out[1 + i];
        for (int y = 0; y < mh; y++)
            for (int x = 0; x < mw; x++) got[y * mw + x] = out[PP_MASK_BASE + y * words + (x >> 7)][x & 127];

        int runs = 1;
        int bst = 0;
        word_t bres = 0, btw[4];
        std::vector<unsigned char> bgot(mh * mw);
        const bool can_band = k == 1 && !(op & PP_CLEAN) && mh >= 3;
        if (can_band) {                  // --- the same frame as 3 uneven bands ---
            const int cut[4] = {0, mh / 5, mh / 5 + mh / 2, mh};
            for (int bi = 0; bi < 3; bi++) {
                const int ya = cut[bi], yb = cut[bi + 1];
                for (int i = 0; i < PP_DEPTH_IN; i++) in[i] = ~word_t(0);
                pack_rows(q, w, y0 + ya, y0 + yb);
                scrub(8 + (yb - ya) * words);
                const int f = op | (bi == 0 ? PP_FIRST : 0) | (bi == 2 ? PP_LAST : 0);
                bst |= pp_layer(in, out, f, h, w, x0, y0, cw, ch, k, thr, tn, ya, yb, y0 + ya);
                for (int y = ya; y < yb; y++)
                    for (int x = 0; x < mw; x++)
                        bgot[y * mw + x] = out[PP_MASK_BASE + (y - ya) * words + (x >> 7)][x & 127];
            }
            bres = out[0];
            for (int i = 0; i < 4; i++) btw[i] = out[1 + i];
            runs = 2;
            banded++;
        }

        bool ok = true;
        int bad_px = 0;
        for (int r = 0; r < runs; r++) {
            const word_t& R = r ? bres : res;
            const word_t* T = r ? btw : tw;
            const unsigned char* G = r ? bgot.data() : got.data();
            const int s = r ? bst : st;
            const int count = R.range(31, 0), comps = R.range(63, 32), largest = R.range(95, 64);
            bool ok1 = s == 0 && (int)R.range(127, 96) == 0 && count == ecount && comps == ecomps &&
                       largest == elargest;
            if (op & PP_TILES)
                for (int n = 0; n < 16; n++)
                    ok1 = ok1 && (int)(unsigned)T[n >> 2].range(32 * (n & 3) + 31, 32 * (n & 3)) == tiles[n];
            if (op & PP_MASK)
                for (int i = 0; i < mh * mw; i++) bad_px += G[i] != em[i];
            if (!ok1 || bad_px)
                std::printf("FAIL %s (%s): status %d count %d/%d comps %d/%d largest %d/%d mask px %d\n", name,
                            r ? "bands" : "one call", s, count, ecount, comps, ecomps, largest, elargest, bad_px);
            ok = ok && ok1 && bad_px == 0;
        }
        if (!(op & (PP_CONN8 | PP_CLEAN)) && k == 1 && x0 == 0 && y0 == 0 && cw == w && ch == h) {
            uint32_t c2 = 0;
            const uint32_t v2 = ai_view_count((const int8_t*)q, w, h, thr, &c2);
            ok = ok && (int)v2 == ecount && (int)c2 == ecomps;
            aiv++;
        }
        cases++;
        fails += ok ? 0 : 1;
    }
    std::fclose(cl);
    std::printf("%d cases (%d also as 3 bands), %d failures, %d also checked against ai_view_count\n", cases, banded,
                fails, aiv);
    std::printf(fails == 0 && cases > 0 ? "PP_CSIM_PASS\n" : "PP_CSIM_FAIL\n");
    return fails != 0 || cases == 0;
}
