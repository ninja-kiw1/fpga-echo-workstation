// Part E step 1: can the f150 vis_layer (unchanged, ai/vis_hls/src/vis_layer.cpp) do SAD block matching with ONE
// call per tracking block? Trick ("masked tile"): a 128 x 128 tile per call; A = 255 everywhere except the
// template (pixels >> 1, 0..127), B = 0 everywhere except the search window (pixels >> 1); cost table
// tab[d] = d for d < 128, 0 for d >= 128. Background pixels of A then always give d >= 128 (cost 0) against
// anything in B (content <= 127, background 0, or a bilinear mix of the two), so every record's SAD is the
// SAD over the template only. This testbench checks that claim for every evaluated candidate against an
// independent reference (template vs window crop, same integer bilinear formula), and counts how often the
// PL's own "best" (S/O ratio, O = whole-tile overlap) differs from the raw-SAD argmin over its records.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "vis_layer.hpp"

static word_t mem[VIS_WORDS];
static const int N = 128, P = 56, T = 16, M = 8;     // tile, template origin, template size, window margin
static const int A_OFF = 8, B_OFF = A_OFF + 1024, T_OFF = B_OFF + 1024, O_OFF = T_OFF + 16;

static void set_byte(int word, int byte, unsigned v) { mem[word].range(8 * byte + 7, 8 * byte) = v & 255; }
static unsigned get32(int word, int k) { return (unsigned)mem[word].range(32 * k + 31, 32 * k); }
static void set_field(int i, int v) { mem[i >> 2].range(32 * (i & 3) + 31, 32 * (i & 3)) = (unsigned)v; }

static int ceil4(int q) { return -((-q) >> 2); }

// reference: SAD over the template of |t - bilinear(w, p + M - q / 4)|, kernel's integer formula
static unsigned ref_sad(const unsigned char* t, const unsigned char* w, int W, int qx, int qy) {
    const int cy = ceil4(qy), cx = ceil4(qx), fy = (-qy) & 3, fx = (-qx) & 3;
    unsigned s = 0;
    for (int y = 0; y < T; y++)
        for (int x = 0; x < T; x++) {
            const int sy = y + M - cy, sx = x + M - cx;
            const int b00 = w[sy * W + sx], b01 = w[sy * W + sx + 1];
            const int b10 = w[(sy + 1) * W + sx], b11 = w[(sy + 1) * W + sx + 1];
            const int h0 = b00 * 4 + fx * (b01 - b00), h1 = b10 * 4 + fx * (b11 - b10);
            const int g = (h0 * 4 + fy * (h1 - h0) + 8) >> 4;
            const int d = t[y * T + x] - g;
            s += d < 0 ? -d : d;
        }
    return s;
}

static bool read_raw(const std::string& p, std::vector<unsigned char>& v, size_t n) {
    FILE* f = std::fopen(p.c_str(), "rb");
    if (!f) return false;
    v.resize(n);
    const bool ok = std::fread(v.data(), 1, n, f) == n;
    std::fclose(f);
    return ok;
}

int main(int argc, char** argv) {
    const std::string dir = argc > 1 ? argv[1] : "data/";
    FILE* pl = std::fopen((dir + "pairs.txt").c_str(), "r");
    if (!pl) return 2;
    struct Mode { const char* name; int mode, radius, subpel, steps; } modes[3] = {
        {"full_r6", 1, 6, 0, 0}, {"tss421_q", 0, 0, 2, 4 | 2 << 8 | 1 << 16}, {"full_r6_q", 1, 6, 2, 0}};
    long recs = 0, bad = 0, blocks = 0, calls = 0, best_diff[3] = {0, 0, 0}, s1_hit[3] = {0, 0, 0}, s1_n = 0;
    char name[64], fa[128], fb[128];
    int n, K;
    while (std::fscanf(pl, "%63s %d %127s %127s %d", name, &n, fa, fb, &K) == 5) {
        std::vector<unsigned char> a, b;
        if (!read_raw(dir + fa, a, (size_t)n * n) || !read_raw(dir + fb, b, (size_t)n * n)) return 3;
        const bool s1 = name[1] == '1';
        for (int k = 0; k < K; k++) {
            int cx, cy;
            if (std::fscanf(pl, "%d %d", &cx, &cy) != 2) return 4;
            const int tx = cx - T / 2, ty = cy - T / 2, W = T + 2 * M;
            unsigned char t[T * T], w[W * W];
            for (int y = 0; y < T; y++)
                for (int x = 0; x < T; x++) t[y * T + x] = a[(ty + y) * n + tx + x] >> 1;
            for (int y = 0; y < W; y++)
                for (int x = 0; x < W; x++) w[y * W + x] = b[(ty - M + y) * n + tx - M + x] >> 1;
            blocks++;
            if (s1) s1_n++;
            for (int mi = 0; mi < 3; mi++) {
                for (int i = 0; i < VIS_WORDS; i++) mem[i] = 0;
                for (int y = 0; y < N; y++)
                    for (int x = 0; x < N; x++) {
                        const int wi = (y * N + x) >> 4, bi = (y * N + x) & 15;
                        const bool inT = y >= P && y < P + T && x >= P && x < P + T;
                        const bool inW = y >= P - M && y < P + T + M && x >= P - M && x < P + T + M;
                        set_byte(A_OFF + wi, bi, inT ? t[(y - P) * T + x - P] : 255);
                        set_byte(B_OFF + wi, bi, inW ? w[(y - P + M) * W + x - P + M] : 0);
                    }
                for (int d = 0; d < 256; d++) set_byte(T_OFF + (d >> 4), d & 15, d < 128 ? d : 0);
                set_field(0, VIS_MAGIC | VIS_OP_REG);
                set_field(1, N);
                set_field(2, modes[mi].mode | modes[mi].subpel << 8 | modes[mi].radius << 16);
                set_field(3, modes[mi].steps);
                set_field(4, A_OFF);
                set_field(5, B_OFF);
                set_field(6, T_OFF);
                set_field(7, O_OFF);
                const int rc = vis_layer(mem, 0);
                calls++;
                if (rc != 0 || get32(O_OFF, 0) != 0) {
                    std::printf("FAIL %s blk %d %s: rc %d\n", name, k, modes[mi].name, rc);
                    bad++;
                    continue;
                }
                const int nrec = (int)get32(O_OFF, 1);
                const int pqx = (int)get32(O_OFF, 2), pqy = (int)get32(O_OFF, 3);
                unsigned bs = 0xFFFFFFFFu;
                int bqx = 0, bqy = 0;
                for (int r = 0; r < nrec; r++) {
                    const int qx = (int)get32(O_OFF + 4 + r, 0), qy = (int)get32(O_OFF + 4 + r, 1);
                    const unsigned s = get32(O_OFF + 4 + r, 2), want = ref_sad(t, w, W, qx, qy);
                    recs++;
                    if (s != want) {
                        if (bad < 10) std::printf("FAIL %s blk %d %s q(%d,%d): PL %u ref %u\n", name, k, modes[mi].name, qx, qy, s, want);
                        bad++;
                    }
                    const int ma = std::abs(qx) + std::abs(qy), mb = std::abs(bqx) + std::abs(bqy);
                    if (s < bs || (s == bs && (ma < mb || (ma == mb && (qx < bqx || (qx == bqx && qy < bqy)))))) {
                        bs = s;
                        bqx = qx;
                        bqy = qy;
                    }
                }
                if (bqx != pqx || bqy != pqy) best_diff[mi]++;
                // set 1: B(y, x) = A(y + 2, x - 3) -> G(p) = B(p - q / 4) = A(p) at q = (-12, +8)
                if (s1 && bqx == -12 && bqy == 8 && bs == 0) s1_hit[mi]++;
            }
        }
    }
    std::printf("blocks %ld, calls %ld, records %ld, SAD mismatches %ld\n", blocks, calls, recs, bad);
    for (int mi = 0; mi < 3; mi++)
        std::printf("%-10s PL best (S/O) != raw-SAD argmin: %ld / %ld; known shift (set 1) exact: %ld / %ld\n",
                    modes[mi].name, best_diff[mi], blocks, s1_hit[mi], s1_n);
    std::printf(bad == 0 ? "BM_ONE_CALL_PER_BLOCK_EXACT\n" : "BM_FAIL\n");
    return bad != 0;
}
