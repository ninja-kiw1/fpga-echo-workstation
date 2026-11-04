/* harness_surf.c -- host run of src/surf3d.c for compare.py.
 * stdin-free: argv[1] = case file (int nz, nr, nc, step, size, nangles; double spacing[3]; double angles[];
 * mask bytes), argv[2] = output prefix: writes <prefix>.json (stats) and <prefix>_<i>.rgb (size x size x 3). */
#include <stdio.h>
#include <stdlib.h>

#include "../../src/surf3d.c"

int main(int argc, char **argv)
{
    FILE         *f = fopen(argv[1], "rb");
    int           hd[6], i;
    double        sp[3], ang[64];
    uint8_t      *m, *rgb;
    size_t        nv;
    surf3d_t      s;
    char          path[512];
    const uint8_t col[3] = {214, 92, 74};

    if (f == NULL || fread(hd, 4, 6, f) != 6 || fread(sp, 8, 3, f) != 3 || hd[5] > 64 ||
        fread(ang, 8, (size_t)hd[5], f) != (size_t)hd[5]) {
        return 2;
    }
    nv = (size_t)hd[0] * hd[1] * hd[2];
    m = malloc(nv);
    if (fread(m, 1, nv, f) != nv) {
        return 2;
    }
    fclose(f);
    if (surf3d_build(m, hd[0], hd[1], hd[2], sp, hd[3], &s) != 0) {
        return 3;
    }
    rgb = malloc((size_t)hd[4] * hd[4] * 3u);
    for (i = 0; i < hd[5]; i++) {
        uint64_t t0 = now_us();

        surf3d_render(&s, ang[i], hd[4], col, rgb);
        snprintf(path, sizeof(path), "%s_%d.rgb", argv[2], i);
        f = fopen(path, "wb");
        fwrite(rgb, 1, (size_t)hd[4] * hd[4] * 3u, f);
        fclose(f);
        fprintf(stderr, "render %d: %llu us\n", i, (unsigned long long)(now_us() - t0));
    }
    snprintf(path, sizeof(path), "%s.json", argv[2]);
    f = fopen(path, "w");
    fprintf(f, "{\"empty\":%d,\"n\":%d,\"dims\":[%d,%d,%d],\"inside\":%ld,\"sz\":%.17g,\"ext\":%.17g,\"vox\":%.17g,"
               "\"build_us\":%u}\n", s.empty, s.n, s.dims[0], s.dims[1], s.dims[2], s.inside, s.sz, s.ext, s.vox,
            s.build_us);
    fclose(f);
    surf3d_free(&s);
    free(m);
    free(rgb);
    return 0;
}
