#ifndef VIS_LAYER_HPP
#define VIS_LAYER_HPP
#include <ap_int.h>

// vis_layer: r2 PL registration engine + graphics overlay (bit-level spec ../visref.py, README ../README.md).
//
// The kernel sees only the tensor BRAM (16384 words of 128 bits, port `mem`); every index is taken mod 16384,
// so no access can leave it. The PS writes a descriptor (8 words = 32 x int32, little-endian) at word `desc`
// and the inputs, starts the kernel, waits for ap_done and reads the results. Returns the status (0 = OK,
// 4 = bad descriptor: nothing is written).
//
// Descriptor field 0 = 0x56495300 | op ('VIS' + op). Offsets are in words.
// op 1 REG : f1 n (128 | 256)  f2 mode (0 three-step, 1 full) | subpel << 8 (0..2) | radius << 16
//            f3 steps s0 | s1 << 8 | s2 << 16 | s3 << 24 (grid pixels, 0-terminated)
//            f4 A offset  f5 B offset (n rows of n / 16 words)  f6 cost table offset (16 words, byte d = tab[d])
//            f7 out offset:
//              out[0] = { status, records, best qx, best qy }   out[1] = { SAD, overlap, path points, 0 }
//              out[2] = path points as int8 (qx, qy) pairs       out[3] = 0
//              out[4 + i] = record i = { qx, qy, SAD, overlap }  (every evaluated candidate, in order)
// op 2 OVL : f1 ow | oh << 16   f2 ya | yb << 16 (band of output rows)   f3 flags (VIS_F_*)
//            f4 lw | lh << 16   f5 logit offset   f6 logit row0 | rows << 16 (rows present at the offset)
//            f7 mw | mh << 16   f8 bits offset    f9 bits row0 | rows << 16
//            f10 base offset (row ya; RGB 3 ow / 16 or grey ow / 16 words per row)
//            f11 out offset (row ya; 3 ow / 16 words per row)   f12 heat lut offset (64 words: entry e = bytes 4e..4e+2, R G B)
//            f13 thr (int8) | band (u8) << 8 | fill alpha k (0..16, in 16ths) << 16
//            f14 fill RGB   f15 unsure RGB   f16 unsure alpha k (0..16)   f17 contour RGB
//            (RGB = R | G << 8 | B << 16; out = (k C + (16 - k) base + 8) >> 4, k = 16 paints C exactly)
// Each band also needs the mask of output rows ya - 1 and yb (contour), so the logit / bit rows present
// must cover the source rows of output rows max(ya - 1, 0) .. min(yb, oh - 1) (visref.ovl_rows_needed).
typedef ap_uint<128> word_t;

static const int VIS_WORDS = 16384;
static const int VIS_MAGIC = 0x56495300;
static const int VIS_OP_REG = 1;
static const int VIS_OP_OVL = 2;
static const int VIS_OK = 0;
static const int VIS_BAD = 4;

enum VisOvlFlags {
    VIS_BASE_NONE = 0, VIS_BASE_GREY = 1, VIS_BASE_RGB = 2, VIS_BASE_HEAT = 3,
    VIS_F_BILINEAR = 4, VIS_F_HAS_LOGIT = 8, VIS_F_HAS_BITS = 16, VIS_F_FILL_FROM_BITS = 32,
    VIS_F_FILL = 64, VIS_F_UNSURE = 128, VIS_F_CONTOUR = 256
};

int vis_layer(word_t* mem, int desc);
#endif
