/* Host harness for src/strain.c (part E step 3): runs the whole PS chain on sequences exported by compare_host.py.
 * in : per sequence  i32 T, f64 sx, f64 sy, u8 lv[128*128], u8 epi[128*128], u8 frames[T*512*512]   (until end of file)
 * out: per sequence  i32 rc, n, path_len, edges, T; i32 path[2*path_len]; f64 pts[2n]; i32 q8[T*2n]; f64 L[T], g[T]
 *     gcc -std=gnu99 -O2 -ffp-contract=off -Wall -Wextra -I../../src harness_strain.c ../../src/strain.c ../../src/bm_sad.c -lm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "strain.h"

int main(int argc, char **argv)
{
    static strain_ws_t w;
    FILE   *fi, *fo;
    int32_t T;

    if (argc != 3 || !(fi = fopen(argv[1], "rb")) || !(fo = fopen(argv[2], "wb"))) {
        fprintf(stderr, "usage: harness_strain in.bin out.bin\n");
        return 2;
    }
    if (strain_ws_init(&w) != STRAIN_OK) {
        return 3;
    }
    uint8_t *lv = malloc(STRAIN_M * STRAIN_M), *epi = malloc(STRAIN_M * STRAIN_M);
    double  *tmp = malloc(sizeof(double) * 5 * STRAIN_NMAX);
    int      nseq = 0;
    while (fread(&T, 4, 1, fi) == 1) {
        double  sx, sy;
        int32_t hdr[5] = {0, 0, 0, 0, T};
        if (fread(&sx, 8, 1, fi) != 1 || fread(&sy, 8, 1, fi) != 1 ||
            fread(lv, 1, STRAIN_M * STRAIN_M, fi) != STRAIN_M * STRAIN_M ||
            fread(epi, 1, STRAIN_M * STRAIN_M, fi) != STRAIN_M * STRAIN_M) {
            return 4;
        }
        const size_t fb = (size_t)STRAIN_W * STRAIN_W;
        uint8_t     *fr = malloc((size_t)T * fb);
        if (fread(fr, 1, (size_t)T * fb, fi) != (size_t)T * fb) {
            return 5;
        }
        const int rc = strain_ed_line(&w, lv, epi, 1, sx, sy);
        hdr[0] = rc;
        if (rc != STRAIN_OK) {
            fwrite(hdr, 4, 5, fo);
            free(fr);
            nseq++;
            continue;
        }
        const int n = w.n;
        int32_t (*q)[2] = malloc(sizeof(int32_t) * 2 * n * T);
        double  *L = malloc(sizeof(double) * T), *g = malloc(sizeof(double) * T);
        int      edges = 0;
        memcpy(q, w.q8, sizeof(int32_t) * 2 * n);
        for (int t = 0; t + 1 < T; t++) {
            memcpy(q + (size_t)(t + 1) * n, q + (size_t)t * n, sizeof(int32_t) * 2 * n);
            const int e = strain_track_step(fr + (size_t)t * fb, fr + (size_t)(t + 1) * fb, n, q + (size_t)(t + 1) * n, 0);
            if (e < 0) {
                return 6;
            }
            edges += e;
        }
        for (int t = 0; t < T; t++) {
            L[t] = strain_line_length((const int32_t (*)[2])(q + (size_t)t * n), n, sx, sy, tmp);
        }
        for (int t = 0; t < T; t++) {
            g[t] = L[t] / L[0] - 1.0;
        }
        hdr[1] = n;
        hdr[2] = w.path_len;
        hdr[3] = edges;
        fwrite(hdr, 4, 5, fo);
        fwrite(w.path, 4, 2 * (size_t)w.path_len, fo);
        fwrite(w.pts, 8, 2 * (size_t)n, fo);
        fwrite(q, 4, 2 * (size_t)n * T, fo);
        fwrite(L, 8, (size_t)T, fo);
        fwrite(g, 8, (size_t)T, fo);
        free(q);
        free(L);
        free(g);
        free(fr);
        nseq++;
    }
    fprintf(stderr, "harness_strain: %d sequences\n", nseq);
    fclose(fo);
    return 0;
}
