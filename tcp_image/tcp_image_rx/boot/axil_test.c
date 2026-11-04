/* axil_test.c -- standalone test of the img_proc_axil register interface.
 *
 * Pushes 512x512 pixels into the PL and pulls the processed ones back through
 * the AXI4-Lite register file, then reports timings and a pixel summary.
 *
 *   reg map (base 0xA000_0000 by default)
 *     0x00 W CTRL     [2:0]=mode [15:8]=threshold; writing resets the frame counter
 *     0x04 R STATUS   [0]=in_ready [1]=out_full [2]=proc_busy [3]=frame_done
 *                     [15:8]=frame min [23:16]=frame max
 *     0x08 W IN_PIX   32-bit pixel {8'h00,R,G,B}; only handshakes while in_ready
 *     0x0C R OUT_PIX  processed pixel; the read pops it
 *     0x10 R VERSION  0x494D_5001
 *
 * Every wait loop is bounded: an AXI write to IN_PIX blocks the CPU until the
 * PL handshakes, so an unbounded spin would wedge the core (that is what took
 * the board off the network during the earlier session).
 *
 * Usage:  ./axil_test [base_hex] [mode] [threshold] [pattern] [npush] [out.raw]
 *         pattern = ramp | white | black | green
 *         npush   = how many pixels to push (default all 262144; 0 = none)
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>

#define DEF_BASE   0xA0000000UL
#define MAP_SIZE   0x1000UL
#define N_PIX      (512u * 512u)

#define R_CTRL    0x00u
#define R_STATUS  0x04u
#define R_IN      0x08u
#define R_OUT     0x0Cu
#define R_VER     0x10u

#define ST_IN_READY   (1u << 0)
#define ST_OUT_FULL   (1u << 1)
#define ST_PROC_BUSY  (1u << 2)
#define ST_FRAME_DONE (1u << 3)

#define SPIN_LIMIT    20000000UL

static volatile uint32_t *reg;

static inline uint32_t r32(uint32_t off) { return reg[off >> 2]; }
static inline void     w32(uint32_t off, uint32_t v) { reg[off >> 2] = v; }

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)(ts.tv_nsec / 1000);
}

int main(int argc, char **argv)
{
    unsigned long base = DEF_BASE;
    int      mode = 0, thr = 128;
    const char *pattern = "ramp";
    const char *dump = NULL;

    if (argc > 1) base = strtoul(argv[1], NULL, 0);
    if (argc > 2) mode = atoi(argv[2]);
    if (argc > 3) thr  = atoi(argv[3]);
    if (argc > 4) pattern = argv[4];
    uint32_t npush = N_PIX;
    if (argc > 5) npush = (uint32_t)strtoul(argv[5], NULL, 0);
    if (argc > 6) dump = argv[6];

    uint32_t pat_const = 0;
    int      pat_fixed = 0;
    if      (!strcmp(pattern, "white")) { pat_const = 0xFFFFFFu; pat_fixed = 1; }
    else if (!strcmp(pattern, "black")) { pat_const = 0x000000u; pat_fixed = 1; }
    else if (!strcmp(pattern, "green")) { pat_const = 0x00FF00u; pat_fixed = 1; }

    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    void *p = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, (off_t)base);
    if (p == MAP_FAILED) { perror("mmap"); return 1; }
    reg = (volatile uint32_t *)p;

    uint32_t ver = r32(R_VER);
    printf("[axil] base=0x%lX  VERSION=0x%08X  %s\n", base, ver,
           (ver == 0x494D5001u) ? "OK" : "*** MISMATCH ***");

    /* input pattern: gray ramp limited to [64,191] so normalization and
     * thresholding both produce something clearly checkable */
    uint32_t *out = (uint32_t *)malloc(N_PIX * 4);
    if (!out) { perror("malloc"); return 1; }

    w32(R_CTRL, (uint32_t)((mode & 7) | (thr << 8)));
    uint32_t st = r32(R_STATUS);
    printf("[axil] mode=%d thr=%d  CTRL=0x%04X  STATUS=0x%08X\n",
           mode, thr, (unsigned)((mode & 7) | (thr << 8)), st);

    /* --- single-pixel poke: write one pixel and read in_pix_r straight back --- */
    if (!strcmp(pattern, "poke")) {
        for (int k = 0; k < 4; k++) {
            uint32_t pv = 0x00123456u + (uint32_t)k;
            printf("[axil] poke write 0x%08X ...\n", pv);
            w32(R_IN, pv);
            printf("[axil]   DBGPIX=0x%08X  DBGCNT=0x%08X  DBGWD=0x%08X  STATUS=0x%08X\n",
                   r32(0x14), r32(0x18), r32(0x1C), r32(R_STATUS));
            usleep(10000);
            printf("[axil]   (after 10 ms) DBGPIX=0x%08X DBGCNT=0x%08X\n",
                   r32(0x14), r32(0x18));
        }
        munmap(p, MAP_SIZE);
        close(fd);
        printf("AXIL_TEST_DONE\n");
        return 0;
    }

    /* ---- push ---- */
    uint64_t t0 = now_us();
    unsigned long stalls = 0;
    for (uint32_t i = 0; i < npush; i++) {
        uint32_t g = 64u + (uint32_t)(((uint64_t)i * 128u) / N_PIX);
        uint32_t pix = (g << 16) | (g << 8) | g;
        if (pat_fixed) pix = pat_const;
        unsigned long k = 0;
        while (!(r32(R_STATUS) & ST_IN_READY)) {
            if (++k > SPIN_LIMIT) {
                printf("[axil] STUCK waiting in_ready at pixel %u\n", i);
                return 2;
            }
            stalls++;
        }
        w32(R_IN, pix);
        if (i < 4) {
            printf("[axil]   push[%u] wrote %06X  STATUS=0x%08X\n",
                   i, pix & 0xFFFFFFu, r32(R_STATUS));
        }
    }
    uint64_t t1 = now_us();
    printf("[axil] after push: STATUS=0x%08X (in_ready=%u out_full=%u busy=%u done=%u)\n",
           r32(R_STATUS), r32(R_STATUS) & 1u, (r32(R_STATUS) >> 1) & 1u,
           (r32(R_STATUS) >> 2) & 1u, (r32(R_STATUS) >> 3) & 1u);
    printf("[axil] DBG in_pix_r=0x%08X  DBG flags=0x%08X\n", r32(0x14), r32(0x18));

    /* ---- pull ---- */
    unsigned long stalls2 = 0;
    for (uint32_t i = 0; i < N_PIX; i++) {
        unsigned long k = 0;
        while (!(r32(R_STATUS) & ST_OUT_FULL)) {
            if (++k > SPIN_LIMIT) {
                printf("[axil] STUCK waiting out_full at pixel %u\n", i);
                return 3;
            }
            stalls2++;
        }
        out[i] = r32(R_OUT);
        if (i < 4) {
            printf("[axil]   pull[%u] got %06X  STATUS=0x%08X\n",
                   i, out[i] & 0xFFFFFFu, r32(R_STATUS));
        }
    }
    uint64_t t2 = now_us();

    st = r32(R_STATUS);
    printf("[axil] push %llu us (%lu stalls)   pull %llu us (%lu stalls)\n",
           (unsigned long long)(t1 - t0), stalls,
           (unsigned long long)(t2 - t1), stalls2);
    printf("[axil] STATUS=0x%08X  min=%u max=%u  frame_done=%u busy=%u\n",
           st, (st >> 8) & 0xFFu, (st >> 16) & 0xFFu,
           (st >> 3) & 1u, (st >> 2) & 1u);

    printf("[axil] first 8 outputs:");
    for (int i = 0; i < 8; i++) printf(" %06X", out[(uint32_t)i] & 0xFFFFFFu);
    printf("\n");
    printf("[axil] checks: px0=%06X px_mid=%06X px_last=%06X\n",
           out[0] & 0xFFFFFFu, out[N_PIX / 2] & 0xFFFFFFu,
           out[N_PIX - 1] & 0xFFFFFFu);

    /* distinct-value histogram (coarse) */
    unsigned long nz = 0, ff = 0;
    for (uint32_t i = 0; i < N_PIX; i++) {
        uint32_t v = out[i] & 0xFFFFFFu;
        if (v == 0) nz++;
        if (v == 0xFFFFFFu) ff++;
    }
    printf("[axil] black=%lu white=%lu of %u\n", nz, ff, (unsigned)N_PIX);

    if (dump) {
        FILE *f = fopen(dump, "wb");
        if (f) {
            unsigned char *rgb = (unsigned char *)malloc(N_PIX * 3);
            if (rgb) {
                for (uint32_t i = 0; i < N_PIX; i++) {
                    uint32_t v = out[i];
                    rgb[i * 3 + 0] = (unsigned char)((v >> 16) & 0xFF);
                    rgb[i * 3 + 1] = (unsigned char)((v >> 8) & 0xFF);
                    rgb[i * 3 + 2] = (unsigned char)(v & 0xFF);
                }
                fwrite(rgb, 1, N_PIX * 3, f);
                free(rgb);
            }
            fclose(f);
            printf("[axil] wrote %s (%u bytes)\n", dump, (unsigned)(N_PIX * 3));
        }
    }

    free(out);
    munmap(p, MAP_SIZE);
    close(fd);
    printf("AXIL_TEST_DONE\n");
    return 0;
}
