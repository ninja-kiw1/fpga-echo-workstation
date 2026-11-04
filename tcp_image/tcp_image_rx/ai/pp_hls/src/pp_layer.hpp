#ifndef PP_LAYER_HPP
#define PP_LAYER_HPP
#include <ap_int.h>

// pp_layer: PL post-processing of the U-Net output (see ../README.md, bit-level spec ../ppref.py).
//
// Input  : int8 logits packed 16 per 128-bit word, row-major, stride = ceil(w / 16) words per row,
//          i.e. the logit array copied as is into the tensor BRAM when w is a multiple of 16.
//          `in` holds frame rows in_row0 .. ; logit (y, x) = byte (x & 15) of in[(y - in_row0) * stride + x / 16].
// Window : (x0, y0, cw, ch) in logit pixels; the mask covers the window, up-sampled by k.
// Mask   : k == 1 -> logit >= thr ; k == 2 / 4 -> bilinear x k of the logits > tn / 2
//          (integer form of tcp_image_ui/ct_volume.upsample_bilinear, CT tiers).
// Bands  : one call handles output rows [ya, yb). PP_FIRST resets the state, PP_LAST finishes and writes
//          the results; calls in between continue the same frame (k == 1, no PP_CLEAN). A whole frame in
//          one call = PP_FIRST | PP_LAST, ya = 0, yb = ch * k.
// Output words (128 bit), written to `out`:
//   out[0]     (PP_LAST) = { status, largest component area, components, mask pixel count } (32 bits each)
//   out[1..4]  (PP_LAST) = 16 int32 tile sums of the raw logits, 4 x 4 tiles of the whole frame (PP_TILES;
//              needs the whole frame: k == 1, full window, w a multiple of 64; = ai_view_fill_reg)
//   out[8 ..]  mask bits of this call's rows (PP_MASK): row (oy - ya) at out[8 + (oy - ya) * words],
//              words = ceil(cw * k / 128), bit j of a word = pixel j (same order as the v6c bit mask)
// Components: 4-connected (8 with PP_CONN8); PP_CLEAN keeps the largest component (ties: first in raster
// order) and fills its holes (background 4-connected, not touching the window border) = echo_ws.clean.
typedef ap_uint<128> word_t;

static const int PP_MAXW = 512;
static const int PP_MAXH = 512;
static const int PP_LBITS = 12;
static const int PP_MAXL = 1 << PP_LBITS;      // provisional labels per frame (real data: <= 1815)
static const int PP_DEPTH_IN = 16384;          // the whole tensor BRAM (256 KB)
static const int PP_DEPTH_OUT = 8 + PP_MAXH * (PP_MAXW / 128);
static const int PP_MASK_BASE = 8;

enum PpOp { PP_MASK = 1, PP_CLEAN = 2, PP_CONN8 = 4, PP_TILES = 8, PP_FIRST = 16, PP_LAST = 32 };
enum PpStatus { PP_OK = 0, PP_FG_OVERFLOW = 1, PP_BG_OVERFLOW = 2, PP_BAD_CONFIG = 4 };

// Returns the status word (also stored in out[0] on PP_LAST); PP_BAD_CONFIG writes only out[0].
int pp_layer(const word_t* in, word_t* out, int op, int h, int w, int x0, int y0, int cw, int ch,
             int k, int thr, int tn, int ya, int yb, int in_row0);
#endif
