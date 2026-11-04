/* harness_card.c -- host run of src/job_simpson.c (P1 1.6 cardiac extension) on request files written by compare.py.
 * The job API is stubbed: RESULT JSON goes to stdout, one line per request. jb_* and job_np_sum are copied from
 * src/job.c (same code).
 *   gcc -std=gnu99 -O2 -Wall -I../../src harness_card.c -lm -o harness_card && ./harness_card req1.bin req2.bin ...
 * request file: u32 param bytes, u32 data bytes, params, data
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "job.h"

void jb_init(jbuf_t *b, char *mem, size_t cap)
{
    b->s = mem;
    b->cap = cap;
    b->len = 0;
    if (cap) {
        mem[0] = '\0';
    }
}

void jb_add(jbuf_t *b, const char *fmt, ...)
{
    va_list ap;
    int     n;

    if (b->len + 1u >= b->cap) {
        return;
    }
    va_start(ap, fmt);
    n = vsnprintf(b->s + b->len, b->cap - b->len, fmt, ap);
    va_end(ap);
    if (n > 0) {
        b->len += ((size_t)n < b->cap - b->len) ? (size_t)n : b->cap - b->len - 1u;
    }
}

uint64_t job_now_us(void)
{
    struct timespec t;

    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}

int job_progress(job_t *j, const char *stage, uint32_t done, uint32_t total, int force)
{
    (void)j, (void)stage, (void)done, (void)total, (void)force;
    return 0;
}

int job_blob(job_t *j, const char *name, const void *p, size_t n, int zlib)
{
    (void)j, (void)name, (void)p, (void)n, (void)zlib;
    return 0;
}

int job_result(job_t *j, const char *json)
{
    (void)j;
    printf("%s\n", json);
    return 0;
}

int job_fail(job_t *j, const char *msg)
{
    (void)j;
    printf("{\"ok\":false,\"error\":\"%s\"}\n", msg);
    return 0;
}

double job_np_sum(const double *a, int n)
{
    double r[8], res;
    int    i, k;

    if (n < 8) {
        res = 0.0;
        for (i = 0; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    if (n <= 128) {
        for (k = 0; k < 8; k++) {
            r[k] = a[k];
        }
        for (i = 8; i < n - (n % 8); i += 8) {
            for (k = 0; k < 8; k++) {
                r[k] += a[i + k];
            }
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    {
        int n2 = n / 2;

        n2 -= n2 % 8;
        return job_np_sum(a, n2) + job_np_sum(a + n2, n - n2);
    }
}

#include "../../src/job_simpson.c"

int main(int argc, char **argv)
{
    int a;

    for (a = 1; a < argc; a++) {
        FILE    *f = fopen(argv[a], "rb");
        uint32_t pb, db;
        uint8_t *buf;
        job_t    j;

        if (f == NULL || fread(&pb, 4, 1, f) != 1 || fread(&db, 4, 1, f) != 1) {
            fprintf(stderr, "bad %s\n", argv[a]);
            return 1;
        }
        buf = (uint8_t *)malloc((size_t)pb + db + 1u);
        if (buf == NULL || fread(buf, 1, (size_t)pb + db, f) != (size_t)pb + db) {
            fprintf(stderr, "short %s\n", argv[a]);
            return 1;
        }
        fclose(f);
        memset(&j, 0, sizeof(j));
        j.kind = JOB_SIMPSON_EF;
        j.param = buf;
        j.param_bytes = pb;
        j.data = buf + pb;
        j.data_bytes = db;
        j.t0_us = job_now_us();
        job_simpson_run(&j);
        fflush(stdout);
        free(buf);
    }
    return 0;
}
