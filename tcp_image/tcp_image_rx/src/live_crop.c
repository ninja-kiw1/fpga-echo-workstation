/* live_crop.c -- see live_crop.h. */
#include "live_crop.h"

#include <stdlib.h>
#include <string.h>

#define CELL 4

int lc_detect(const uint8_t *const *frames, int K, int W, int H, int thr, lc_box_t *box)
{
    int      CW = (W + CELL - 1) / CELL, CH = (H + CELL - 1) / CELL, NC = CW * CH, i, k, x, y;
    uint8_t *mn = (uint8_t *)malloc((size_t)W * H), *mx = (uint8_t *)malloc((size_t)W * H);
    uint8_t *cell = (uint8_t *)calloc((size_t)NC, 1u);
    int     *lab = (int *)malloc((size_t)NC * sizeof(int)), *stack = (int *)malloc((size_t)NC * sizeof(int));
    int      best = 0, bx0 = 0, by0 = 0, bx1 = -1, by1 = -1, rc = -1;

    if (K < 2 || mn == NULL || mx == NULL || cell == NULL || lab == NULL || stack == NULL) {
        goto out;
    }
    memcpy(mn, frames[0], (size_t)W * H);
    memcpy(mx, frames[0], (size_t)W * H);
    for (k = 1; k < K; k++) {
        const uint8_t *f = frames[k];

        for (i = 0; i < W * H; i++) {
            mn[i] = f[i] < mn[i] ? f[i] : mn[i];
            mx[i] = f[i] > mx[i] ? f[i] : mx[i];
        }
    }
    for (y = 0; y < H; y++) {
        for (x = 0; x < W; x++) {
            if (mx[y * W + x] - mn[y * W + x] > thr) {
                cell[(y / CELL) * CW + x / CELL] = 1u;
            }
        }
    }
    /* largest 8-connected group of moving cells (scan order, explicit stack) */
    for (i = 0; i < NC; i++) {
        lab[i] = 0;
    }
    for (i = 0; i < NC; i++) {
        int sp = 0, n = 0, x0 = CW, y0 = CH, x1 = -1, y1 = -1;

        if (!cell[i] || lab[i]) {
            continue;
        }
        lab[i] = 1;
        stack[sp++] = i;
        while (sp > 0) {
            int c = stack[--sp], cx = c % CW, cy = c / CW, dx, dy;

            n++;
            x0 = cx < x0 ? cx : x0;
            x1 = cx > x1 ? cx : x1;
            y0 = cy < y0 ? cy : y0;
            y1 = cy > y1 ? cy : y1;
            for (dy = -1; dy <= 1; dy++) {
                for (dx = -1; dx <= 1; dx++) {
                    int nx = cx + dx, ny = cy + dy;

                    if (nx >= 0 && nx < CW && ny >= 0 && ny < CH && cell[ny * CW + nx] && !lab[ny * CW + nx]) {
                        lab[ny * CW + nx] = 1;
                        stack[sp++] = ny * CW + nx;
                    }
                }
            }
        }
        if (n > best) {
            best = n;
            bx0 = x0;
            by0 = y0;
            bx1 = x1;
            by1 = y1;
        }
    }
    if (best == 0) {
        goto out;
    }
    {
        /* cell box -> exact pixel box of the moving pixels inside it */
        int px0 = W, py0 = H, px1 = -1, py1 = -1, side, cx2, cy2;

        for (y = by0 * CELL; y < (by1 + 1) * CELL && y < H; y++) {
            for (x = bx0 * CELL; x < (bx1 + 1) * CELL && x < W; x++) {
                if (mx[y * W + x] - mn[y * W + x] > thr) {
                    px0 = x < px0 ? x : px0;
                    px1 = x > px1 ? x : px1;
                    py0 = y < py0 ? y : py0;
                    py1 = y > py1 ? y : py1;
                }
            }
        }
        box->bx = px0;
        box->by = py0;
        box->bw = px1 - px0 + 1;
        box->bh = py1 - py0 + 1;
        box->cells = best;
        side = box->bw > box->bh ? box->bw : box->bh;
        side = side < W ? side : W;
        side = side < H ? side : H;
        cx2 = px0 + px1 + 1;                  /* twice the centre */
        cy2 = py0 + py1 + 1;
        box->x = (cx2 - side) / 2;
        box->y = (cy2 - side) / 2;
        box->x = box->x < 0 ? 0 : (box->x + side > W ? W - side : box->x);
        box->y = box->y < 0 ? 0 : (box->y + side > H ? H - side : box->y);
        box->side = side;
    }
    rc = 0;
out:
    free(mn);
    free(mx);
    free(cell);
    free(lab);
    free(stack);
    return rc;
}

/* overlap of source pixel j ([N j, N j + N)) with output pixel i ([S i, S i + S)), units of 1 / N px */
static void spans(int S, int N, int i, int *j0, int *j1, int *w0, int *w1)
{
    int a = S * i, b = S * (i + 1);

    *j0 = a / N;
    *j1 = (b - 1) / N;
    *w0 = (*j0 + 1) * N - a;                 /* first source pixel */
    *w1 = b - *j1 * N;                       /* last source pixel */
    if (*j0 == *j1) {
        *w0 = *w1 = S;
    }
}

void lc_crop_resize(const uint8_t *frame, int stride, int step, const lc_box_t *box, uint8_t *out, uint32_t *tmp)
{
    lc_crop_resize_n(frame, stride, step, box, out, tmp, LC_OUT);
}

void lc_crop_resize_n(const uint8_t *frame, int stride, int step, const lc_box_t *box, uint8_t *out, uint32_t *tmp,
                      int N)
{
    int      S = box->side, r, i, j;
    uint64_t den = (uint64_t)S * (uint64_t)S;

    /* horizontal: tmp[r][i] = sum_j overlap_x(i, j) * p(r, j) for every source row r of the square */
    for (r = 0; r < S; r++) {
        const uint8_t *row = frame + (size_t)(box->y + r) * (size_t)stride + (size_t)box->x * (size_t)step;
        uint32_t      *t = tmp + (size_t)r * N;

        for (i = 0; i < N; i++) {
            int      j0, j1, w0, w1;
            uint32_t s;

            spans(S, N, i, &j0, &j1, &w0, &w1);
            if (j0 == j1) {
                t[i] = (uint32_t)w0 * row[(size_t)j0 * step];
                continue;
            }
            s = (uint32_t)w0 * row[(size_t)j0 * step] + (uint32_t)w1 * row[(size_t)j1 * step];
            for (j = j0 + 1; j < j1; j++) {
                s += (uint32_t)N * row[(size_t)j * step];
            }
            t[i] = s;
        }
    }
    /* vertical, then round half up */
    for (i = 0; i < N; i++) {
        int j0, j1, w0, w1, x;

        spans(S, N, i, &j0, &j1, &w0, &w1);
        for (x = 0; x < N; x++) {
            uint64_t s;

            if (j0 == j1) {
                s = (uint64_t)w0 * tmp[(size_t)j0 * N + x];
            } else {
                s = (uint64_t)w0 * tmp[(size_t)j0 * N + x] + (uint64_t)w1 * tmp[(size_t)j1 * N + x];
                for (j = j0 + 1; j < j1; j++) {
                    s += (uint64_t)N * tmp[(size_t)j * N + x];
                }
            }
            out[i * N + x] = (uint8_t)((s + den / 2u) / den);
        }
    }
}
