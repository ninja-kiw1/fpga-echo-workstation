/* job_render.c -- 3-D surface views on the board (P1 1.4): JOB_RENDER3D and the ct_seg render option.
 *
 * The reconstruction (surf3d.c) is the C port of ct_volume.Surface / render (float; on the host it matches the
 * Python pixel for pixel, on the board it is accepted by tolerance): crop, ~cubic voxels, Gaussian smoothing,
 * surface + normals once, then one orthographic z-buffer view per angle (rotation about the body axis,
 * 0 = seen from the front, the patient's right on the left of the image).
 *
 * JOB_RENDER3D params (32 + 8 n bytes): u16 nz, u16 size (0 = 320), u8 n angles (0 = 12 evenly spaced),
 *   u8 step (0 = 2), u16 0, f64 spacing[3] (dx, dy, dz mm), f64 angles[n] (degrees)
 * data: the mask bit-packed like ct_seg's "mask_final" (z, y, x of nz x 512 x 512; bit i & 7 of byte i >> 3)
 * reply: blob "render3d" = n views of size x size RGB (zlib), result {"ok":true,"surface":{...}}
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "image_proto.h"
#include "job.h"
#include "job_render.h"
#include "surf3d.h"

#define N 512

int job_render3d(job_t *j, const uint8_t *mask, int nz, const double spc[3], int n_angles, const double *angles,
                 int size, int step, jbuf_t *b)
{
    static const uint8_t col[3] = {214, 92, 74};      /* ct_volume.Surface.render default colour */
    surf3d_t s;
    uint8_t *img;
    double   ang[RENDER_MAX_ANGLES];
    uint64_t t;
    int      i, rc;

    if (n_angles <= 0) {
        n_angles = 12;
    }
    if (n_angles > RENDER_MAX_ANGLES) {
        n_angles = RENDER_MAX_ANGLES;
    }
    if (angles != NULL) {
        memcpy(ang, angles, sizeof(double) * (size_t)n_angles);
    } else {                                           /* ct_seg's views: evenly spaced, 360 / n apart */
        for (i = 0; i < n_angles; i++) {
            ang[i] = 360.0 * i / n_angles;
        }
    }
    if (size <= 0) {
        size = 320;
    }
    if (size > RENDER_MAX_SIZE || step < 1 || step > 8) {
        jb_add(b, ",\"surface\":{\"error\":\"bad size / step\"}");
        return -2;
    }
    if (job_progress(j, "render3d", 0u, (uint32_t)n_angles, 1) != 0) {
        return -1;
    }
    if (surf3d_build(mask, nz, N, N, spc, step, &s) != 0) {
        jb_add(b, ",\"surface\":{\"error\":\"out of memory\"}");
        return -2;
    }
    img = (uint8_t *)malloc((size_t)n_angles * size * size * 3u);
    if (img == NULL) {
        surf3d_free(&s);
        jb_add(b, ",\"surface\":{\"error\":\"out of memory\"}");
        return -2;
    }
    t = job_now_us();
    for (i = 0; i < n_angles; i++) {
        surf3d_render(&s, ang[i], size, col, img + (size_t)i * size * size * 3u);
        if (job_progress(j, "render3d", (uint32_t)i + 1u, (uint32_t)n_angles, 0) != 0) {
            free(img);
            surf3d_free(&s);
            return -1;
        }
    }
    t = job_now_us() - t;
    rc = job_blob(j, "render3d", img, (size_t)n_angles * size * size * 3u, 1);
    free(img);
    if (rc == 0) {
        jb_add(b, ",\"surface\":{\"empty\":%s,\"points\":%d,\"inside\":%ld,\"dims\":[%d,%d,%d],\"sz\":%.17g,"
                  "\"ext\":%.17g,\"size\":%d,\"step\":%d,\"build_ms\":%.2f,\"render_ms\":%.2f,\"angles\":[",
               s.empty ? "true" : "false", s.n, s.inside, s.dims[0], s.dims[1], s.dims[2], s.sz, s.ext, size, step,
               s.build_us / 1000.0, (double)t / 1000.0 / n_angles);
        for (i = 0; i < n_angles; i++) {
            jb_add(b, "%s%.6g", i ? "," : "", ang[i]);
        }
        jb_add(b, "]}");
    }
    surf3d_free(&s);
    return rc;
}

int job_render_run(job_t *j)
{
    const uint8_t *p = j->param;
    double         spc[3], ang[RENDER_MAX_ANGLES];
    char           js[2048];
    jbuf_t         b;
    uint8_t       *m;
    int            nz, size, na, step, i, rc;
    size_t         nv;

    if (j->param_bytes < 32u) {
        return job_fail(j, "render3d needs 32 bytes of params");
    }
    nz = p[0] | p[1] << 8;
    size = p[2] | p[3] << 8;
    na = p[4];
    step = p[5] ? p[5] : 2;
    memcpy(spc, p + 8, sizeof(spc));                    /* aarch64: little-endian IEEE, as the PC packs it */
    if (na > RENDER_MAX_ANGLES || j->param_bytes < 32u + 8u * (uint32_t)na) {
        return job_fail(j, "render3d: angles missing or too many");
    }
    for (i = 0; i < na; i++) {
        memcpy(&ang[i], p + 32 + 8 * i, 8u);
    }
    nv = (size_t)nz * N * N;
    if (nz < 1 || nz > 1024 || j->data_bytes != nv / 8u) {
        return job_fail(j, "render3d: data must be nz x 512 x 512 bits");
    }
    m = (uint8_t *)malloc(nv);
    if (m == NULL) {
        return job_fail(j, "render3d: out of memory");
    }
    for (nv = 0; nv < (size_t)nz * N * N; nv++) {
        m[nv] = (uint8_t)((j->data[nv >> 3] >> (nv & 7u)) & 1u);
    }
    jb_init(&b, js, sizeof(js));
    jb_add(&b, "{\"ok\":true,\"nz\":%d", nz);
    rc = job_render3d(j, m, nz, spc, na, ang, size, step, &b);
    free(m);
    if (rc == -2) {
        return job_fail(j, "render3d failed (bad size / step or out of memory)");
    }
    if (rc != 0 || j->failed) {
        return -1;
    }
    jb_add(&b, ",\"total_ms\":%.2f}", (double)(job_now_us() - j->t0_us) / 1000.0);
    return job_result(j, js);
}
