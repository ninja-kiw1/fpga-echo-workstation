/* vis_ai.c -- PS driver for the vis_layer PL kernel (see vis_ai.h). */
#include "vis_ai.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "unet_ai.h"

/* register map: ai/build/vis_ip_r2/drivers/vis_layer_v1_0/src/xvis_layer_hw.h (m_axi addresses are 32 bit) */
#define R_AP_CTRL   0x00u
#define R_RETURN    0x10u
#define R_MEM       0x18u
#define R_DESC      0x20u
#define AP_START    (1u << 0)
#define AP_DONE     (1u << 1)
#define AP_IDLE     (1u << 2)
#define MAP_SIZE    0x10000u
#define BRAM_WORDS  16384u
#define WORD_BYTES  16u
#define DESC_WORDS  8u
#define SPIN_LIMIT  200000000ul
#define MAGIC       0x56495300u
#define OP_REG      1u
#define OP_OVL      2u

const uint8_t vis_absdiff_table[256] = {
      0,   0,   1,   1,   2,   2,   3,   3,   5,   5,   5,   5,   6,   6,   7,   7,
      8,   8,   9,   9,  10,  10,  11,  11,  12,  12,  12,  12,  14,  14,  15,  15,
     16,  16,  17,  17,  18,  18,  19,  19,  20,  20,  21,  21,  21,  21,  23,  23,
     24,  24,  25,  25,  26,  26,  27,  27,  28,  28,  29,  29,  31,  31,  31,  31,
     32,  32,  33,  33,  34,  34,  35,  35,  36,  36,  37,  37,  38,  38,  38,  38,
     40,  40,  41,  41,  42,  42,  43,  43,  44,  44,  45,  45,  46,  46,  48,  48,
     48,  48,  49,  49,  50,  50,  51,  51,  52,  52,  53,  53,  54,  54,  55,  55,
     57,  57,  57,  57,  58,  58,  59,  59,  60,  60,  61,  61,  62,  62,  63,  63,
     64,  64,  64,  64,  66,  66,  67,  67,  68,  68,  69,  69,  70,  70,  71,  71,
     72,  72,  74,  74,  74,  74,  75,  75,  76,  76,  77,  77,  78,  78,  79,  79,
     80,  80,  81,  81,  81,  81,  83,  83,  84,  84,  85,  85,  86,  86,  87,  87,
     88,  88,  89,  89,  90,  90,  90,  90,  92,  92,  93,  93,  94,  94,  95,  95,
     96,  96,  97,  97,  98,  98, 100, 100, 100, 100, 101, 101, 102, 102, 103, 103,
    104, 104, 105, 105, 106, 106, 107, 107, 107, 107, 109, 109, 110, 110, 111, 111,
    112, 112, 113, 113, 114, 114, 115, 115, 117, 117, 117, 117, 118, 118, 119, 119,
    120, 120, 121, 121, 122, 122, 123, 123, 124, 124, 126, 126, 126, 126, 127, 127,
};

static int                g_fd = -1;
static void              *g_map;
static volatile uint32_t *g_reg;

#ifdef VIS_HOST_TEST
/* host test (test/vis/): the HLS C++ kernel runs on the shim's BRAM instead of the PL */
extern int vis_host_kernel(uint8_t *bram, uint32_t desc_word);
static uint32_t g_fake_regs[MAP_SIZE / 4];
int vis_host_init(void) { g_reg = g_fake_regs; return 0; }
#endif

static uint64_t now_us(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

static inline void wr(uint32_t off, uint32_t v) { g_reg[off >> 2] = v; }
static inline uint32_t rd(uint32_t off) { return g_reg[off >> 2]; }

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int vis_firmware_ok(void)
{
    char  line[256], hash[65] = "";
    FILE *p = popen("sha256sum " VIS_FIRMWARE " 2>/dev/null", "r");
    FILE *f;
    int   ok = 0;

    if (p == NULL) {
        return 0;
    }
    if (fgets(line, sizeof(line), p) != NULL && strlen(line) >= 64u) {
        memcpy(hash, line, 64u);
        hash[64] = '\0';
    }
    pclose(p);
    f = fopen(VIS_FIRMWARE_LIST, "r");
    if (hash[0] == '\0' || f == NULL) {
        if (f != NULL) {
            fclose(f);
        }
        return 0;
    }
    while (fgets(line, sizeof(line), f) != NULL) {
        if (strncmp(line, hash, 64u) == 0) {
            ok = 1;
        }
    }
    fclose(f);
    return ok;
}

int vis_init(uint64_t ctrl_base)
{
    uint32_t v;

    if (g_reg != NULL) {
        return 0;
    }
    if (unet_ready() == 0 || unet_bram_ptr() == NULL) {
        printf("[vis] needs unet_init() first (shared tensor BRAM)\n");
        return -1;
    }
    if ((unet_bram_phys() >> 32) != 0u) {
        printf("[vis] tensor BRAM above 4 GB: vis_layer has 32-bit addresses\n");
        return -1;
    }
    g_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (g_fd < 0) {
        printf("[vis] open /dev/mem: %s\n", strerror(errno));
        return -1;
    }
    g_map = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)ctrl_base);
    if (g_map == MAP_FAILED) {
        printf("[vis] mmap 0x%llX: %s\n", (unsigned long long)ctrl_base, strerror(errno));
        close(g_fd);
        g_fd = -1;
        g_map = NULL;
        return -1;
    }
    g_reg = (volatile uint32_t *)g_map;
    v = rd(R_AP_CTRL);                         /* read once before any write (handoff §9.6 ③) */
    if ((v & AP_IDLE) == 0u) {
        printf("[vis] ap_idle=0 (CTRL=0x%08X): not the vis_layer bitstream?\n", v);
        vis_close();
        return -1;
    }
    printf("[vis] vis_layer ready @0x%llX\n", (unsigned long long)ctrl_base);
    return 0;
}

int vis_ready(void) { return g_reg != NULL; }

void vis_close(void)
{
    if (g_map != NULL && g_map != MAP_FAILED) {
        munmap(g_map, MAP_SIZE);
    }
    if (g_fd >= 0) {
        close(g_fd);
    }
    g_map = NULL;
    g_reg = NULL;
    g_fd = -1;
}

/* Normal-NC writes may still sit in the write buffer when AP_START goes out on the other port: read back
 * the last word written (same master, same slave: ordered), as pp_ai.c / unet_ai.c bram_commit(). */
static void bram_commit(uint32_t word)
{
    volatile const uint32_t *p = (volatile const uint32_t *)(const void *)(unet_bram_ptr() + (size_t)word * WORD_BYTES);

    (void)p[0];
}

/* descriptor (32 x u32) at word 0, written after the inputs, so reading back its last word orders everything */
static int run_kernel(const uint32_t f[32], uint32_t *pl_us)
{
    uint8_t      *bram = unet_bram_ptr();
    unsigned long spin = 0;
    uint64_t      t0;
    uint32_t      ret;
    int           i;

    for (i = 0; i < 32; i++) {
        put32(bram + 4 * i, f[i]);
    }
    bram_commit(DESC_WORDS - 1u);
#ifdef VIS_HOST_TEST
    (void)spin;
    (void)t0;
    ret = (uint32_t)vis_host_kernel(bram, 0u);
    (void)pl_us;
    return ret == 0u ? 0 : -3;
#endif
    wr(R_MEM, (uint32_t)unet_bram_phys());
    wr(R_DESC, 0u);
    t0 = now_us();
    wr(R_AP_CTRL, AP_START);
    while ((rd(R_AP_CTRL) & AP_DONE) == 0u) {
        if (++spin > SPIN_LIMIT) {
            printf("[vis] TIMEOUT waiting ap_done\n");
            return -2;
        }
    }
    if (pl_us != NULL) {
        *pl_us += (uint32_t)(now_us() - t0);
    }
    ret = rd(R_RETURN);
    return ret == 0u ? 0 : -3;
}

int vis_run_raw(uint32_t desc_word, uint32_t *ret, uint32_t *pl_us)
{
    unsigned long spin = 0;
    uint64_t      t0;

    if (g_reg == NULL || desc_word > BRAM_WORDS - 5u) {
        return -1;
    }
    bram_commit(BRAM_WORDS - 1u);
    wr(R_MEM, (uint32_t)unet_bram_phys());
    wr(R_DESC, desc_word);
    t0 = now_us();
    wr(R_AP_CTRL, AP_START);
    while ((rd(R_AP_CTRL) & AP_DONE) == 0u) {
        if (++spin > SPIN_LIMIT) {
            return -2;
        }
    }
    if (pl_us != NULL) {
        *pl_us = (uint32_t)(now_us() - t0);
    }
    *ret = rd(R_RETURN);
    return 0;
}

int vis_reg(const uint8_t *a, const uint8_t *b, int n, int mode, int radius, const uint8_t steps[4], int subpel,
            const uint8_t tab[256], vis_reg_result_t *res)
{
    uint8_t       *bram = unet_bram_ptr();
    const uint32_t fw = (uint32_t)(n * n / 16), a_off = DESC_WORDS, b_off = a_off + fw, t_off = b_off + fw;
    const uint32_t o_off = t_off + 16u;
    uint64_t       t0 = now_us();
    uint32_t       f[32];
    int            rc, i;

    if (g_reg == NULL || a == NULL || b == NULL || tab == NULL || res == NULL || (n != 128 && n != 256) ||
        mode < 0 || mode > 1 || subpel < 0 || subpel > 2 || o_off + 4u + VIS_MAX_RECORDS > BRAM_WORDS) {
        return -1;
    }
    memset(res, 0, sizeof(*res));
    memset(f, 0, sizeof(f));
    f[0] = MAGIC | OP_REG;
    f[1] = (uint32_t)n;
    f[2] = (uint32_t)mode | (uint32_t)subpel << 8 | (uint32_t)(radius & 255) << 16;
    f[3] = (uint32_t)steps[0] | (uint32_t)steps[1] << 8 | (uint32_t)steps[2] << 16 | (uint32_t)steps[3] << 24;
    f[4] = a_off;
    f[5] = b_off;
    f[6] = t_off;
    f[7] = o_off;
    memcpy(bram + (size_t)a_off * WORD_BYTES, a, (size_t)fw * WORD_BYTES);
    memcpy(bram + (size_t)b_off * WORD_BYTES, b, (size_t)fw * WORD_BYTES);
    memcpy(bram + (size_t)t_off * WORD_BYTES, tab, 256u);
    rc = run_kernel(f, &res->pl_us);
    if (rc == 0) {
        uint8_t w[4 * WORD_BYTES];

        memcpy(w, bram + (size_t)o_off * WORD_BYTES, sizeof(w));
        res->nrec = (int)get32(w + 4);
        res->qx = (int)(int32_t)get32(w + 8);
        res->qy = (int)(int32_t)get32(w + 12);
        res->sad = get32(w + 16);
        res->overlap = get32(w + 20);
        res->npath = (int)get32(w + 24);
        for (i = 0; i < 8; i++) {
            res->path[i][0] = (int8_t)w[32 + 2 * i];
            res->path[i][1] = (int8_t)w[33 + 2 * i];
        }
        if (res->nrec < 0 || res->nrec > VIS_MAX_RECORDS || res->npath < 1 || res->npath > 8) {
            rc = -3;
        } else {
            for (i = 0; i < res->nrec; i++) {
                uint8_t r[WORD_BYTES];

                memcpy(r, bram + ((size_t)o_off + 4u + (size_t)i) * WORD_BYTES, sizeof(r));
                res->rec[i].qx = (int8_t)get32(r);
                res->rec[i].qy = (int8_t)get32(r + 4);
                res->rec[i].sad = get32(r + 8);
                res->rec[i].overlap = get32(r + 12);
            }
        }
    }
    res->total_us = (uint32_t)(now_us() - t0);
    return rc;
}

static uint32_t rgb24(const uint8_t c[3]) { return (uint32_t)c[0] | (uint32_t)c[1] << 8 | (uint32_t)c[2] << 16; }

/* nearest source rows of output rows lo..hi (as the kernel checks them) */
static void src_rows(int lo, int hi, int n, int oh, int *r0, int *r1)
{
    *r0 = (int)((long)lo * n / oh);
    *r1 = (int)((long)hi * n / oh);
}

int vis_overlay(const vis_ovl_t *c, const uint8_t *base, const int8_t *logits, const uint8_t *bits,
                const uint8_t lut[256][3], uint8_t *rgb, uint32_t *pl_us, uint32_t *calls)
{
    uint8_t   *bram = unet_bram_ptr();
    const int  kind = c->flags & 3, has_l = (c->flags & VIS_F_HAS_LOGIT) != 0, has_b = (c->flags & VIS_F_HAS_BITS) != 0;
    const int  lstride = has_l ? c->lw / 16 : 0, mbytes = has_b ? (c->mw + 127) / 128 * 16 : 0;
    const int  bbytes = kind == (int)VIS_BASE_RGB ? 3 * c->ow : (kind == (int)VIS_BASE_GREY ? c->ow : 0);
    const int  obytes = 3 * c->ow;
    const uint32_t lut_off = DESC_WORDS, data0 = lut_off + (kind == (int)VIS_BASE_HEAT ? 64u : 0u);
    int        rows, ya;

    if (g_reg == NULL || c == NULL || rgb == NULL || c->ow < 16 || c->ow > 512 || c->ow % 16 || c->oh < 1 ||
        c->oh > 512 || (has_l && (logits == NULL || c->lw % 16 || c->lw > c->ow || c->lh < 1 || c->lh > c->oh)) ||
        (has_b && (bits == NULL || c->mw < 1 || c->mw > c->ow || c->mh < 1 || c->mh > c->oh)) ||
        (bbytes && base == NULL) || (kind == (int)VIS_BASE_HEAT && lut == NULL)) {
        return -1;
    }
    /* the largest band that fits: inputs of its rows (+ the rows above / below for the contour) + output */
    for (rows = c->oh; rows > 0; rows--) {
        const long src_l = has_l ? ((long)(rows + 2) * c->lh / c->oh + 3) * lstride : 0;
        const long src_m = has_b ? ((long)(rows + 2) * c->mh / c->oh + 3) * (mbytes / 16) : 0;
        const long need = (long)data0 + src_l + src_m + (long)rows * (bbytes + obytes) / 16;

        if (need <= (long)BRAM_WORDS) {
            break;
        }
    }
    if (rows < 1) {
        return -1;
    }
    if (kind == (int)VIS_BASE_HEAT) {
        int e;

        for (e = 0; e < 256; e++) {
            uint8_t *p = bram + (size_t)lut_off * WORD_BYTES + 4u * (size_t)e;

            p[0] = lut[e][0];
            p[1] = lut[e][1];
            p[2] = lut[e][2];
            p[3] = 0u;
        }
    }
    for (ya = 0; ya < c->oh; ya += rows) {
        const int yb = ya + rows < c->oh ? ya + rows : c->oh;
        const int rlo = ya > 0 ? ya - 1 : 0, rhi = yb < c->oh ? yb : c->oh - 1;
        int       l0 = 0, l1 = -1, m0 = 0, m1 = -1, rc;
        uint32_t  off = data0, loff = 0, moff = 0, boff = 0, ooff, f[32];

        if (has_l) {
            src_rows(rlo, rhi, c->lh, c->oh, &l0, &l1);
            loff = off;
            memcpy(bram + (size_t)off * WORD_BYTES, logits + (size_t)l0 * c->lw, (size_t)(l1 - l0 + 1) * c->lw);
            off += (uint32_t)((l1 - l0 + 1) * lstride);
        }
        if (has_b) {
            src_rows(rlo, rhi, c->mh, c->oh, &m0, &m1);
            moff = off;
            memcpy(bram + (size_t)off * WORD_BYTES, bits + (size_t)m0 * mbytes, (size_t)(m1 - m0 + 1) * mbytes);
            off += (uint32_t)((m1 - m0 + 1) * (mbytes / 16));
        }
        if (bbytes) {
            boff = off;
            memcpy(bram + (size_t)off * WORD_BYTES, base + (size_t)ya * bbytes, (size_t)(yb - ya) * bbytes);
            off += (uint32_t)((yb - ya) * bbytes / 16);
        }
        ooff = off;
        off += (uint32_t)((yb - ya) * obytes / 16);
        if (off > BRAM_WORDS) {
            return -1;
        }
        memset(f, 0, sizeof(f));
        f[0] = MAGIC | OP_OVL;
        f[1] = (uint32_t)c->ow | (uint32_t)c->oh << 16;
        f[2] = (uint32_t)ya | (uint32_t)yb << 16;
        f[3] = (uint32_t)c->flags;
        f[4] = (uint32_t)c->lw | (uint32_t)c->lh << 16;
        f[5] = loff;
        f[6] = has_l ? ((uint32_t)l0 | (uint32_t)(l1 - l0 + 1) << 16) : 0u;
        f[7] = (uint32_t)c->mw | (uint32_t)c->mh << 16;
        f[8] = moff;
        f[9] = has_b ? ((uint32_t)m0 | (uint32_t)(m1 - m0 + 1) << 16) : 0u;
        f[10] = boff;
        f[11] = ooff;
        f[12] = lut_off;
        f[13] = ((uint32_t)c->thr & 255u) | ((uint32_t)c->band & 255u) << 8 | ((uint32_t)c->fill_a & 0x1FFu) << 16;
        f[14] = rgb24(c->fill);
        f[15] = rgb24(c->unsure);
        f[16] = (uint32_t)c->unsure_a & 0x1FFu;
        f[17] = rgb24(c->contour);
        rc = run_kernel(f, pl_us);
        if (calls != NULL) {
            (*calls)++;
        }
        if (rc != 0) {
            return rc;
        }
        memcpy(rgb + (size_t)ya * obytes, bram + (size_t)ooff * WORD_BYTES, (size_t)(yb - ya) * obytes);
    }
    return 0;
}
