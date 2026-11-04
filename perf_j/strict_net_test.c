#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "unet_ai.h"

static int load_exact(const char *path, void *dst, size_t count) {
    FILE *fp = fopen(path, "rb");
    if (!fp) { perror(path); return -1; }
    size_t got = fread(dst, 1, count, fp);
    int extra = fgetc(fp);
    fclose(fp);
    return got == count && extra == EOF ? 0 : -1;
}

int main(int argc, char **argv) {
    if (argc != 6) { fprintf(stderr, "usage: rgb i8 model resolution samples\n"); return 2; }
    int model = atoi(argv[3]), res = atoi(argv[4]), samples = atoi(argv[5]);
    if (res < 32 || res > 512 || samples < 1) return 2;
    size_t n = (size_t)res * res;
    uint8_t *rgb = malloc(n * 3);
    int8_t *want = malloc(n), *got = malloc(n);
    if (!rgb || !want || !got || load_exact(argv[1], rgb, n * 3) || load_exact(argv[2], want, n)) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (unet_init(0xA0010000ull, 0xA0020000ull, 0xB0000000ull) ||
        unet_select_model(model) || unet_set_size(res, res)) return 1;
    for (int iter = -2; iter < samples; iter++) {
        unet_stats_t stats;
        if (unet_run(rgb, got, &stats)) return 1;
        size_t bad = 0, first = 0;
        for (size_t p = 0; p < n; p++) if (got[p] != want[p]) { if (!bad) first = p; bad++; }
        printf("CHECK model=%d res=%d iter=%d mismatch=%zu/%zu\n", model, res, iter, bad, n);
        if (bad) {
            FILE *fp = fopen("first_failure_logits.i8", "wb");
            if (fp) { fwrite(got, 1, n, fp); fclose(fp); }
            printf("FAIL first_pixel=%zu wanted=%d got=%d\n", first, want[first], got[first]);
            unet_close(); return 1;
        }
        if (iter >= 0) printf("SAMPLE {\"model\":%d,\"res\":%d,\"sample\":%d,\"ap_us\":%u,\"put_us\":%u,\"get_us\":%u,\"run_us\":%u,\"strips\":%u}\n",
                model, res, iter, stats.ap_us, stats.put_us, stats.get_us, stats.run_us, stats.strips);
    }
    unet_close(); free(rgb); free(want); free(got);
    puts("STRICT_NET_PASS");
    return 0;
}
