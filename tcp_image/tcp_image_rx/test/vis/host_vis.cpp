// host_vis.cpp -- src/vis_ai.c (the PS driver) against the vis_layer C++ kernel on a fake tensor BRAM.
// Reads cases written by check_driver.py, writes results for it to compare with visref / registration.py.
//   reg  case: n, mode, radius, steps[4], subpel, A, B          -> best, path, records
//   ovl  case: vis_ovl_t fields, base, logits, bits, lut        -> rgb
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "vis_layer.hpp"
extern "C" {
#include "vis_ai.h"
int vis_host_init(void);
int vis_host_kernel(uint8_t *bram, uint32_t desc_word);
int unet_ready(void);
uint8_t *unet_bram_ptr(void);
uint64_t unet_bram_phys(void);
}

static uint8_t g_bram[16384 * 16];
static word_t g_mem[16384];
int unet_ready(void) { return 1; }
uint8_t *unet_bram_ptr(void) { return g_bram; }
uint64_t unet_bram_phys(void) { return 0xB0000000ull; }

int vis_host_kernel(uint8_t *bram, uint32_t desc_word) {
    for (int i = 0; i < 16384; i++) {
        word_t w = 0;
        for (int b = 0; b < 16; b++) w.range(8 * b + 7, 8 * b) = bram[16 * i + b];
        g_mem[i] = w;
    }
    const int ret = vis_layer(g_mem, (int)desc_word);
    for (int i = 0; i < 16384; i++)
        for (int b = 0; b < 16; b++) bram[16 * i + b] = (uint8_t)g_mem[i].range(8 * b + 7, 8 * b);
    return ret;
}

static std::vector<uint8_t> rd(FILE* f, size_t n) {
    std::vector<uint8_t> v(n);
    if (n && fread(v.data(), 1, n, f) != n) exit(3);
    return v;
}

static vis_reg_result_t g_res;

int main(int argc, char** argv) {
    FILE* f = fopen(argv[1], "rb");
    FILE* o = fopen(argv[2], "wb");
    vis_host_init();
    int kind;
    while (fread(&kind, 4, 1, f) == 1) {
        if (kind == 1) {
            int h[9];
            fread(h, 4, 9, f);
            const int n = h[0];
            uint8_t steps[4] = {(uint8_t)h[3], (uint8_t)h[4], (uint8_t)h[5], (uint8_t)h[6]};
            auto a = rd(f, n * n), b = rd(f, n * n), tab = rd(f, 256);
            int rc = vis_reg(a.data(), b.data(), n, h[1], h[2], steps, h[7], tab.data(), &g_res);
            fwrite(&rc, 4, 1, o);
            int w[6] = {g_res.qx, g_res.qy, (int)g_res.sad, (int)g_res.overlap, g_res.npath, g_res.nrec};
            fwrite(w, 4, 6, o);
            fwrite(g_res.path, 1, 16, o);
            for (int i = 0; i < g_res.nrec; i++) {
                int r[4] = {g_res.rec[i].qx, g_res.rec[i].qy, (int)g_res.rec[i].sad, (int)g_res.rec[i].overlap};
                fwrite(r, 4, 4, o);
            }
        } else {
            int h[16];
            fread(h, 4, 16, f);
            vis_ovl_t c;
            memset(&c, 0, sizeof(c));
            c.ow = h[0]; c.oh = h[1]; c.flags = h[2]; c.lw = h[3]; c.lh = h[4]; c.mw = h[5]; c.mh = h[6];
            c.thr = h[7]; c.band = h[8]; c.fill_a = h[9]; c.unsure_a = h[10];
            for (int k = 0; k < 3; k++) {
                c.fill[k] = (uint8_t)(h[11] >> (8 * k));
                c.unsure[k] = (uint8_t)(h[12] >> (8 * k));
                c.contour[k] = (uint8_t)(h[13] >> (8 * k));
            }
            const int kb = c.flags & 3;
            auto base = rd(f, kb == 2 ? 3 * c.ow * c.oh : (kb == 1 ? c.ow * c.oh : 0));
            auto lg = rd(f, (c.flags & 8) ? c.lw * c.lh : 0);
            auto bits = rd(f, (c.flags & 16) ? (c.mw + 127) / 128 * 16 * c.mh : 0);
            auto lut = rd(f, kb == 3 ? 768 : 0);
            std::vector<uint8_t> rgb(3 * c.ow * c.oh, 0xEE);
            uint32_t pl = 0, calls = 0;
            int rc = vis_overlay(&c, base.data(), (const int8_t*)lg.data(), bits.data(),
                                 kb == 3 ? (const uint8_t(*)[3])lut.data() : nullptr, rgb.data(), &pl, &calls);
            fwrite(&rc, 4, 1, o);
            int cc = (int)calls;
            fwrite(&cc, 4, 1, o);
            fwrite(rgb.data(), 1, rgb.size(), o);
        }
    }
    fclose(o);
    return 0;
}
