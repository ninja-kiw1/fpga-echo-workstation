// pp_layer.cpp -- PL post-processing of the U-Net logits (interface: pp_layer.hpp), v3: one pixel per clock,
// packed int8 input (16 logits per word, the logit array copied as is) and row bands with state kept between
// calls (a 512 x 512 frame packed is the whole 256 KB tensor BRAM, so it is sent in two halves).
// v2 = pp_layer_v2.cpp.txt (word-per-pixel input, whole frame only); results are identical.
//
// v1 (pp_layer_v1.cpp.txt) ran five loops per row and did union-find inside the pixel loop (~9 clocks / pixel).
// v2 keeps the same results bit for bit (spec ../ppref.py) with one II=1 loop per row:
//   * provisional labels come from a purely local rule (U, L, UL, UR, else new) that never looks at the
//     union-find table, so the pixel loop has no variable-latency work;
//   * at most one equivalence per pixel is needed (the others are already implied by earlier pixels:
//     U~UL, U~UR and L~UL are neighbours in rows already scanned), it goes into a per-row list and the
//     unions run after the row;
//   * component sizes are accumulated per provisional label as runs, added to the table after the row;
//   * mask, count, packed output bits and (full-frame k=1) tile sums are produced in the same loop;
//   * the two line buffers alternate by row parity instead of being copied.
// Union-find roots are still the smallest label, so the root is the component's first pixel in raster order.
#include "pp_layer.hpp"

typedef ap_uint<PP_LBITS> lab_t;
typedef ap_uint<PP_LBITS + 1> nlab_t;        // 0 = no label, else label + 1
typedef ap_uint<19> area_t;                  // up to 512 * 512
typedef ap_uint<10> len_t;                   // run length, up to 512

static ap_int<8> byte_of(const word_t& wd, int i) {
#pragma HLS INLINE
    return (ap_int<8>)wd.range(8 * i + 7, 8 * i);
}

static lab_t find_root(const lab_t par[PP_MAXL], lab_t a) {
FIND:
    while (par[a] != a) {
#pragma HLS LOOP_TRIPCOUNT max=16
        a = par[a];
    }
    return a;
}

static void unite_all(lab_t par[PP_MAXL], const lab_t ea[PP_MAXW], const lab_t eb[PP_MAXW], int n) {
UNITE:
    for (int i = 0; i < n; i++) {
#pragma HLS LOOP_TRIPCOUNT max=64
        const lab_t ra = find_root(par, ea[i]), rb = find_root(par, eb[i]);
        if (ra < rb) {
            par[rb] = ra;
        } else if (rb < ra) {
            par[ra] = rb;
        }
    }
}

// parent[l] <= l, so one forward sweep makes every entry point at its root (and sums sizes into it)
static void flatten(lab_t par[PP_MAXL], area_t area[PP_MAXL], ap_uint<1> bor[PP_MAXL], int n, bool use_area) {
FLAT:
    for (int l = 0; l < n; l++) {
#pragma HLS LOOP_TRIPCOUNT max=4096
        const lab_t p = par[l];
        if (p != lab_t(l)) {
            const lab_t r = par[p];
            par[l] = r;
            if (use_area) area[r] = area[r] + area[l];
            else bor[r] = bor[r] | bor[l];
        }
    }
}

// Local labelling rule shared by every pass: returns label + 1 (0 = background) and the one equivalence this
// pixel adds (has_ev: ea ~ eb). New labels come from `next`; past PP_MAXL the label saturates and ovf is set.
static nlab_t local_label(bool fg, bool conn8, nlab_t up, nlab_t left, nlab_t ul, nlab_t ur, int& next,
                          bool& is_new, bool& ovf, bool& has_ev, lab_t& ea, lab_t& eb) {
#pragma HLS INLINE
    is_new = false;
    has_ev = false;
    ea = 0;
    eb = 0;
    if (!fg) return 0;
    if (!conn8) {
        ul = 0;
        ur = 0;
    }
    nlab_t lab;
    if (up != 0) {
        lab = up;
        if (left != 0 && left != up) { has_ev = true; ea = up - 1; eb = left - 1; }
    } else if (left != 0) {
        lab = left;
        if (ur != 0 && ur != left) { has_ev = true; ea = left - 1; eb = ur - 1; }
    } else if (ul != 0) {
        lab = ul;
        if (ur != 0 && ur != ul) { has_ev = true; ea = ul - 1; eb = ur - 1; }
    } else if (ur != 0) {
        lab = ur;
    } else if (next < PP_MAXL) {
        lab = next + 1;
        next++;
        is_new = true;
    } else {
        lab = PP_MAXL;                           // label PP_MAXL-1; result flagged invalid
        ovf = true;
    }
    return lab;
}

int pp_layer(const word_t* in, word_t* out, int op, int h, int w, int x0, int y0, int cw, int ch,
             int k, int thr, int tn, int ya, int yb, int in_row0) {
#ifdef PP_ONE_BUNDLE
    // r2 (P1 1.3): one m_axi adapter for both pointers (AXI keeps separate read / write channels), one SI less on
    // ai_data_ic; the C code, the register map and the results are unchanged (build: run_hls_pp.bat with PP_ONE_BUNDLE=1)
#pragma HLS INTERFACE m_axi port=in offset=slave bundle=gmem depth=16384 max_read_burst_length=16 num_read_outstanding=2 max_write_burst_length=16 num_write_outstanding=2
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem depth=2056
#else
#pragma HLS INTERFACE m_axi port=in offset=slave bundle=gmem_in depth=16384 max_read_burst_length=16 num_read_outstanding=2 max_write_burst_length=2 num_write_outstanding=1
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_out depth=2056 max_write_burst_length=16 num_write_outstanding=2 max_read_burst_length=2 num_read_outstanding=1
#endif
#pragma HLS INTERFACE s_axilite port=in bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=op bundle=control
#pragma HLS INTERFACE s_axilite port=h bundle=control
#pragma HLS INTERFACE s_axilite port=w bundle=control
#pragma HLS INTERFACE s_axilite port=x0 bundle=control
#pragma HLS INTERFACE s_axilite port=y0 bundle=control
#pragma HLS INTERFACE s_axilite port=cw bundle=control
#pragma HLS INTERFACE s_axilite port=ch bundle=control
#pragma HLS INTERFACE s_axilite port=k bundle=control
#pragma HLS INTERFACE s_axilite port=thr bundle=control
#pragma HLS INTERFACE s_axilite port=tn bundle=control
#pragma HLS INTERFACE s_axilite port=ya bundle=control
#pragma HLS INTERFACE s_axilite port=yb bundle=control
#pragma HLS INTERFACE s_axilite port=in_row0 bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    static lab_t fpar[PP_MAXL], bpar[PP_MAXL];
    static area_t farea[PP_MAXL];                // foreground sizes (per provisional label, then per root)
    static ap_uint<1> bbor[PP_MAXL];             // background: touches the window border
    static nlab_t fA[PP_MAXW], fB[PP_MAXW], bA[PP_MAXW], bB[PP_MAXW];   // line buffers, by row parity
    static lab_t ea[PP_MAXW], eb[PP_MAXW];       // this row's equivalences
    static lab_t rlab[PP_MAXW];                  // this row's runs (label, length)
    static len_t rlen[PP_MAXW];
    static ap_int<8> q0[PP_MAXW], q1[PP_MAXW];   // source rows for up-sampling
    static word_t rowbuf[PP_MAXW / 16];          // one packed input row
    // frame state kept between band calls (PP_FIRST resets it)
    static int nf, count, ty, trow;
    static bool fovf;
    static int tsum[16];
#pragma HLS ARRAY_PARTITION variable=tsum complete

    const int kshift = (k == 4) ? 2 : (k == 2) ? 1 : 0;
    const int oh = ch * k, ow = cw * k, words = (ow + 127) >> 7;
    const bool first = (op & PP_FIRST) != 0, last = (op & PP_LAST) != 0;
    const bool whole = first && last && ya == 0 && yb == oh;
    const bool bad = h < 1 || w < 1 || h > PP_MAXH || w > PP_MAXW || x0 < 0 || y0 < 0 || cw < 1 || ch < 1 ||
                     x0 + cw > w || y0 + ch > h || (k != 1 && k != 2 && k != 4) || ow > PP_MAXW ||
                     oh > PP_MAXH || thr < -128 || thr > 127 || ya < 0 || yb > oh || ya >= yb ||
                     in_row0 < 0 || (!whole && (k != 1 || (op & PP_CLEAN) != 0));
    if (bad) {
        word_t s = 0;
        s.range(127, 96) = PP_BAD_CONFIG;
        out[0] = s;
        return PP_BAD_CONFIG;
    }
    const bool conn8 = (op & PP_CONN8) != 0, clean = (op & PP_CLEAN) != 0, mask_out = (op & PP_MASK) != 0;
    const bool tiles_on = (op & PP_TILES) != 0 && (h & 3) == 0 && (w & 63) == 0;   // tiles = whole words
    // tile sums ride along pass 1 when it reads every logit of the frame once (k = 1, whole frame)
    const bool tiles_fused = tiles_on && k == 1 && x0 == 0 && y0 == 0 && cw == w && ch == h;
    const int d = 2 * k, ds = kshift + 1, th = h >> 2, tw = w >> 2, stride = (w + 15) >> 4;

    if (first) {
        nf = 0;
        count = 0;
        ty = 0;
        trow = 0;
        fovf = false;
        for (int i = 0; i < 16; i++) tsum[i] = 0;
    }
    int nb = 0, comps = 0, best = -1;
    area_t best_area = 0;
    bool bovf = false;
    int cached_i0 = -1;

PASS:
    for (int pass = 1; pass <= 3; pass++) {
        if (pass > 1 && !clean) break;
        if (pass == 2 && !last) break;
        int rf = 0, rb = 0;                      // label counters of the replays (restart at 0)
        const int r0 = (pass == 1) ? ya : 0, r1 = (pass == 1) ? yb : oh;
    ROW:
        for (int oy = r0; oy < r1; oy++) {
#pragma HLS LOOP_TRIPCOUNT max=512
            // ---- source rows for up-sampling (reloaded only when the pair changes) ----
            int fy = 0;
            if (k != 1) {
                int ny = 2 * (y0 * k + oy) + 1 - k;
                if (ny < 0) ny = 0;
                const int ry0 = ny >> ds;
                fy = ny & (d - 1);
                if (ry0 != cached_i0) {
                    const int ry1 = (ry0 + 1 < h) ? ry0 + 1 : h - 1;
                    for (int r = 0; r < 2; r++) {
                    LOADW:
                        for (int j = 0; j < stride; j++) {
#pragma HLS LOOP_TRIPCOUNT max=8
#pragma HLS PIPELINE II=1
                            rowbuf[j] = in[((r == 0 ? ry0 : ry1) - in_row0) * stride + j];
                        }
                    LOADQ:
                        for (int x = 0; x < w; x++) {
#pragma HLS LOOP_TRIPCOUNT max=128
#pragma HLS PIPELINE II=1
                            const ap_int<8> v = byte_of(rowbuf[x >> 4], x & 15);
                            if (r == 0) q0[x] = v; else q1[x] = v;
                        }
                    }
                    cached_i0 = ry0;
                }
            } else {
            LOADR:
                for (int j = 0; j < stride; j++) {
#pragma HLS LOOP_TRIPCOUNT max=32
#pragma HLS PIPELINE II=1
                    rowbuf[j] = in[(y0 + oy - in_row0) * stride + j];
                }
            }
            const bool odd = (oy & 1) != 0, first_row = (oy == 0), edge_row = first_row || (oy == oh - 1);
            const int obase = PP_MASK_BASE + (oy - r0) * words;
            const bool emit = mask_out && ((pass == 1 && !clean) || pass == 3);
            int nev = 0, nrun = 0;
            lab_t runl = 0;
            len_t runn = 0;
            nlab_t f_up = 0, f_ul = 0, f_left = 0, b_up = 0, b_ul = 0, b_left = 0;
            lab_t last_a = 0, last_b = 0;
            bool have_last = false;
            word_t bits = 0;
            int tx = 0, tcnt = 0;
            if (!first_row) {
                f_up = odd ? fA[0] : fB[0];
                b_up = odd ? bA[0] : bB[0];
            }
        PIX:
            for (int x = 0; x < ow; x++) {
#pragma HLS LOOP_TRIPCOUNT max=512
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=fA inter false
#pragma HLS DEPENDENCE variable=fB inter false
#pragma HLS DEPENDENCE variable=bA inter false
#pragma HLS DEPENDENCE variable=bB inter false
#pragma HLS DEPENDENCE variable=fpar inter false
#pragma HLS DEPENDENCE variable=bpar inter false
#pragma HLS DEPENDENCE variable=farea inter false
#pragma HLS DEPENDENCE variable=bbor inter false
                // ---- mask ----
                bool mv;
                ap_int<8> lv = 0;
                if (k == 1) {
                    lv = byte_of(rowbuf[(x0 + x) >> 4], (x0 + x) & 15);
                    mv = lv >= thr;
                } else {
                    int nx = 2 * (x0 * k + x) + 1 - k;
                    if (nx < 0) nx = 0;
                    const int j0 = nx >> ds, g = nx & (d - 1);
                    const int j1 = (j0 + 1 < w) ? j0 + 1 : w - 1;
                    const int r0 = q0[j0] * (d - fy) + q1[j0] * fy;
                    const int r1 = q0[j1] * (d - fy) + q1[j1] * fy;
                    mv = 2 * (r0 * (d - g) + r1 * g) > tn * d * d;
                }
                if (tiles_fused && pass == 1) {
                    tsum[ty * 4 + tx] += lv;
                    if (++tcnt == tw) {
                        tcnt = 0;
                        tx++;
                    }
                }
                // ---- foreground: local rule (all passes) ----
                const nlab_t f_ur = (!first_row && x + 1 < ow) ? (odd ? fA[x + 1] : fB[x + 1]) : nlab_t(0);
                bool f_new, f_ev, b_new = false, b_ev = false;
                lab_t fa, fb, ba = 0, bb2 = 0;
                const nlab_t fl = (pass == 1)
                    ? local_label(mv, conn8, f_up, f_left, f_ul, f_ur, nf, f_new, fovf, f_ev, fa, fb)
                    : local_label(mv, conn8, f_up, f_left, f_ul, f_ur, rf, f_new, fovf, f_ev, fa, fb);
                if (odd) fB[x] = fl; else fA[x] = fl;
                bool px = mv;                          // output pixel of this pass
                if (pass == 1) {
                    count += mv ? 1 : 0;
                    if (f_new) {
                        fpar[fl - 1] = fl - 1;
                        farea[fl - 1] = 0;
                    }
                    if (f_ev && !(have_last && fa == last_a && fb == last_b)) {
                        ea[nev] = fa;
                        eb[nev] = fb;
                        nev++;
                        last_a = fa;
                        last_b = fb;
                        have_last = true;
                    }
                    // runs of equal provisional labels -> sizes after the row
                    if (fl != 0 && runn != 0 && lab_t(fl - 1) == runl) {
                        runn++;
                    } else {
                        if (runn != 0) {
                            rlab[nrun] = runl;
                            rlen[nrun] = runn;
                            nrun++;
                        }
                        runl = fl - 1;
                        runn = (fl != 0) ? 1 : 0;
                    }
                } else {
                    // ---- passes 2/3: keep the largest component, label / fill its background ----
                    const bool kept = fl != 0 && best >= 0 && fpar[fl - 1] == lab_t(best);
                    const nlab_t b_ur = (!first_row && x + 1 < ow) ? (odd ? bA[x + 1] : bB[x + 1]) : nlab_t(0);
                    const nlab_t bl = (pass == 2)
                        ? local_label(!kept, false, b_up, b_left, b_ul, b_ur, nb, b_new, bovf, b_ev, ba, bb2)
                        : local_label(!kept, false, b_up, b_left, b_ul, b_ur, rb, b_new, bovf, b_ev, ba, bb2);
                    if (odd) bB[x] = bl; else bA[x] = bl;
                    if (pass == 2) {
                        const bool on_edge = edge_row || x == 0 || x == ow - 1;
                        if (b_new) {
                            bpar[bl - 1] = bl - 1;
                            bbor[bl - 1] = on_edge ? 1 : 0;
                        } else if (bl != 0 && on_edge) {
                            bbor[bl - 1] = 1;
                        }
                        if (b_ev && !(have_last && ba == last_a && bb2 == last_b)) {
                            ea[nev] = ba;
                            eb[nev] = bb2;
                            nev++;
                            last_a = ba;
                            last_b = bb2;
                            have_last = true;
                        }
                    }
                    px = kept || (pass == 3 && bl != 0 && bbor[bpar[bl - 1]] == 0);
                    b_ul = b_up;
                    b_up = b_ur;
                    b_left = bl;
                }
                f_ul = f_up;
                f_up = f_ur;
                f_left = fl;
                // ---- packed output bits ----
                if (px) bits[x & 127] = 1;
                if ((x & 127) == 127 || x == ow - 1) {
                    if (emit) out[obase + (x >> 7)] = bits;
                    bits = 0;
                }
            }
            if (tiles_fused && pass == 1 && ++trow == th) {   // trow / ty continue across band calls
                trow = 0;
                ty++;
            }
            if (pass == 1) {
                if (runn != 0) {
                    rlab[nrun] = runl;
                    rlen[nrun] = runn;
                    nrun++;
                }
            AREA:
                for (int i = 0; i < nrun; i++) {
#pragma HLS LOOP_TRIPCOUNT max=32
                    farea[rlab[i]] = farea[rlab[i]] + rlen[i];
                }
                unite_all(fpar, ea, eb, nev);
            } else if (pass == 2) {
                unite_all(bpar, ea, eb, nev);
            }
        }
        if (pass == 1 && last) {
            flatten(fpar, farea, bbor, nf, true);
        ROOTS:
            for (int l = 0; l < nf; l++) {
#pragma HLS LOOP_TRIPCOUNT max=4096
#pragma HLS PIPELINE II=1
                if (fpar[l] == lab_t(l)) {
                    comps++;
                    if (farea[l] > best_area) {      // strict: ties keep the earliest component
                        best_area = farea[l];
                        best = l;
                    }
                }
            }
        } else if (pass == 2) {
            flatten(bpar, farea, bbor, nb, false);
        }
    }

    if (!last) return fovf ? PP_FG_OVERFLOW : PP_OK;

    // ---- tile sums when they could not ride along pass 1 (whole frame in one call) ----
    if (tiles_on && !tiles_fused) {
    TY:
        for (int ty = 0; ty < 4; ty++) {
        TX:
            for (int tx = 0; tx < 4; tx++) {
                int acc = 0;
            TROW:
                for (int yy = ty * th; yy < (ty + 1) * th; yy++) {
#pragma HLS LOOP_TRIPCOUNT max=128
                TWORD:
                    for (int j = (tx * tw) >> 4; j < ((tx + 1) * tw) >> 4; j++) {
#pragma HLS LOOP_TRIPCOUNT max=8
#pragma HLS PIPELINE II=1
                        const word_t wd = in[(yy - in_row0) * stride + j];
                        int s16 = 0;
                        for (int i = 0; i < 16; i++) s16 += byte_of(wd, i);
                        acc += s16;
                    }
                }
                tsum[ty * 4 + tx] = acc;
            }
        }
    }
    for (int i = 0; i < 4; i++) {
        word_t t = 0;
        for (int j = 0; j < 4; j++) t.range(32 * j + 31, 32 * j) = (ap_uint<32>)(tiles_on ? tsum[4 * i + j] : 0);
        out[1 + i] = t;
    }

    const int status = (fovf ? PP_FG_OVERFLOW : 0) | (bovf ? PP_BG_OVERFLOW : 0);
    word_t s = 0;
    s.range(31, 0) = count;
    s.range(63, 32) = comps;
    s.range(95, 64) = (int)best_area;
    s.range(127, 96) = status;
    out[0] = s;
    return status;
}
