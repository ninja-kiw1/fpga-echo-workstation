/* job.c -- case jobs (control header v7 "HM"): request parsing, reply framing, dispatch. See job.h. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "job.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

#include "image_proto.h"
#include "pp_ai.h"
#include "progpkg.h"
#include "unet_ai.h"

uint64_t job_now_us(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

uint32_t job_crc32(const uint8_t *p, size_t n)
{
    uLong c = crc32(0L, Z_NULL, 0);

    while (n > 0u) {                       /* zlib's length argument is a uInt */
        uInt k = (n > 0x40000000u) ? 0x40000000u : (uInt)n;

        c = crc32(c, p, k);
        p += k;
        n -= k;
    }
    return (uint32_t)c;
}

void jb_init(jbuf_t *b, char *mem, size_t cap)
{
    b->s = mem;
    b->cap = cap;
    b->len = 0u;
    if (cap > 0u) {
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

static int recv_all(int fd, uint8_t *buf, size_t len)
{
    size_t got = 0u;

    while (got < len) {
        ssize_t n = recv(fd, buf + got, len - got, 0);

        if (n == 0) {
            return -2;
        }
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        got += (size_t)n;
    }
    return 0;
}

static int send_raw(int fd, const uint8_t *buf, size_t len)
{
    size_t sent = 0u;

    while (sent < len) {
        ssize_t n = send(fd, buf + sent, len - sent, MSG_NOSIGNAL);

        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        sent += (size_t)n;
    }
    return 0;
}

/* one message = header + up to two payload pieces (the CRC covers both, in order) */
static int send_msg(job_t *j, unsigned type, const void *a, size_t na, const void *b, size_t nb)
{
    uint8_t h[JOB_MSG_HDR_BYTES];
    uLong   c = crc32(0L, Z_NULL, 0);

    if (j->failed) {
        return -1;
    }
    if (na > 0u) {
        c = crc32(c, (const Bytef *)a, (uInt)na);
    }
    if (nb > 0u) {
        c = crc32(c, (const Bytef *)b, (uInt)nb);
    }
    memset(h, 0, sizeof(h));
    proto_put_le16(h + 0u, JOB_MSG_MAGIC);
    h[2] = (uint8_t)type;
    proto_put_le32(h + 4u, (uint32_t)(na + nb));
    proto_put_le32(h + 8u, (uint32_t)c);
    proto_put_le32(h + 12u, j->seq++);
    if (send_raw(j->fd, h, sizeof(h)) != 0 || (na > 0u && send_raw(j->fd, (const uint8_t *)a, na) != 0) ||
        (nb > 0u && send_raw(j->fd, (const uint8_t *)b, nb) != 0)) {
        j->failed = 1;
        return -1;
    }
    return 0;
}

int job_progress(job_t *j, const char *stage, uint32_t done, uint32_t total, int force)
{
    char     s[160];
    uint64_t now = job_now_us();
    int      n;

    if (j->failed) {
        return -1;
    }
    if (!force && now - j->last_progress_us < (uint64_t)JOB_PROGRESS_MS * 1000u) {
        return 0;
    }
    j->last_progress_us = now;
    n = snprintf(s, sizeof(s), "{\"stage\":\"%s\",\"done\":%u,\"total\":%u,\"ms\":%.1f}", stage, (unsigned)done,
                 (unsigned)total, (double)(now - j->t0_us) / 1000.0);
    return send_msg(j, JOB_MSG_PROGRESS, s, (size_t)n, NULL, 0u);
}

int job_progress_json(job_t *j, const char *json)
{
    j->last_progress_us = job_now_us();
    return send_msg(j, JOB_MSG_PROGRESS, json, strlen(json), NULL, 0u);
}

int job_blob(job_t *j, const char *name, const void *p, size_t n, int zlib)
{
    uint8_t  head[1u + 255u + 5u];
    size_t   nl = strlen(name), hn;
    uint8_t *z = NULL;
    uLongf   zn = 0u;
    int      rc;

    if (nl > 255u) {
        nl = 255u;
    }
    if (zlib && n > 0u) {
        zn = compressBound((uLong)n);
        z = (uint8_t *)malloc(zn);
        if (z == NULL || compress2(z, &zn, (const Bytef *)p, (uLong)n, 1) != Z_OK || zn >= n) {
            free(z);
            z = NULL;
        }
    }
    head[0] = (uint8_t)nl;
    memcpy(head + 1u, name, nl);
    hn = 1u + nl;
    head[hn] = (z != NULL) ? 1u : 0u;
    proto_put_le32(head + hn + 1u, (uint32_t)n);
    hn += 5u;
    rc = (z != NULL) ? send_msg(j, JOB_MSG_BLOB, head, hn, z, zn) : send_msg(j, JOB_MSG_BLOB, head, hn, p, n);
    free(z);
    return rc;
}

int job_result(job_t *j, const char *json)
{
    return send_msg(j, JOB_MSG_RESULT, json, strlen(json), NULL, 0u);
}

int job_fail(job_t *j, const char *msg)
{
    char s[400];

    snprintf(s, sizeof(s), "{\"ok\":false,\"error\":\"%s\"}", msg);
    printf("[job] %u failed: %s\n", (unsigned)j->kind, msg);
    return job_result(j, s);
}

/* job_np_sum: moved to echo_num.c (10-24), still declared in job.h */

/* software split of the U-Net time of the running job (job_pl_split_json); one job at a time */
static uint64_t g_ap_us, g_put_us, g_get_us, g_zero_us, g_run_us;

void job_pl_split_reset(void)
{
    g_ap_us = g_put_us = g_get_us = g_zero_us = g_run_us = 0u;
}

void job_pl_split_json(jbuf_t *b)
{
    jb_add(b, ",\"pl_split\":{\"ap_ms\":%.3f,\"put_ms\":%.3f,\"get_ms\":%.3f,\"zero_ms\":%.3f,\"other_ms\":%.3f,"
              "\"source\":\"software timers (unet_stats_t)\"}", g_ap_us / 1000.0, g_put_us / 1000.0, g_get_us / 1000.0,
           g_zero_us / 1000.0, (double)(g_run_us - g_ap_us - g_put_us - g_get_us - g_zero_us) / 1000.0);
}

void job_unet_account(const unet_stats_t *st)
{
    g_ap_us += st->ap_us;
    g_put_us += st->put_us;
    g_get_us += st->get_us;
    g_zero_us += st->zero_us;
    g_run_us += st->run_us;
}

int job_unet(unsigned model, int shift, const uint8_t *rgb, int8_t *logits, uint32_t *run_us)
{
    unet_stats_t st;

    if (unet_ready() == 0 || prog_select(model) != 0 || unet_set_shift(shift) != 0) {
        return -1;
    }
    if (unet_run(rgb, logits, &st) != 0) {
        return -1;
    }
    if (run_us != NULL) {
        *run_us += st.run_us;
    }
    job_unet_account(&st);
    return 0;
}

static int job_caps(job_t *j)
{
    char   s[768];
    jbuf_t b;

    jb_init(&b, s, sizeof(s));
    jb_add(&b, "{\"ok\":true,\"job_version\":%u,\"jobs\":[\"caps\",\"echo_ef\",\"simpson_ef\",\"strain\"]",
           JOB_VERSION);
    jb_add(&b, ",\"vis\":false,\"cardiac\":true,\"server\":\"v5.27\",\"echo_modes\":[0,1,2,3],\"live_roll\":true,"
               "\"live_roll_camus\":true,\"live_f1_engine\":true,"
               "\"live_sources\":[\"file\",\"v4l2\"],\"heart\":[\"wall_motion\",\"right_heart\"]");
    jb_add(&b, ",\"pp\":%s,\"ai\":%s,\"models\":%u,\"cores\":%ld,\"max_data\":%u}", pp_ready() ? "true" : "false",
           unet_ready() ? "true" : "false", (unsigned)UNET_MODELS_COUNT, sysconf(_SC_NPROCESSORS_ONLN),
           (unsigned)JOB_MAX_DATA);
    return job_result(j, s);
}

int job_handle(int cfd, const uint8_t *first2, uint32_t *frames_run, int verbose)
{
    uint8_t  h[JOB_HDR_BYTES], end[4];
    uint8_t *buf = NULL;
    uint32_t pn, dn, crc, status;
    job_t    j;
    int      rc = 0;

    memcpy(h, first2, 2u);
    if (recv_all(cfd, h + 2u, JOB_HDR_BYTES - 2u) != 0) {
        printf("[job] header incomplete\n");
        return -1;
    }
    memset(&j, 0, sizeof(j));
    j.fd = cfd;
    j.kind = h[2];
    j.flags = proto_get_le32(h + 4u);
    pn = proto_get_le32(h + 8u);
    dn = proto_get_le32(h + 12u);
    crc = proto_get_le32(h + 16u);
    if (h[3] != JOB_VERSION || pn > 4096u || dn > JOB_MAX_DATA) {
        printf("[job] bad header: version %u params %u data %u\n", (unsigned)h[3], (unsigned)pn, (unsigned)dn);
        return -1;
    }
    if (pn + dn > 0u) {
        buf = (uint8_t *)malloc((size_t)pn + dn);
        if (buf == NULL) {
            printf("[job] cannot allocate %u bytes\n", (unsigned)(pn + dn));
            return -1;
        }
        if (recv_all(cfd, buf, (size_t)pn + dn) != 0) {
            printf("[job] upload incomplete (%u bytes expected)\n", (unsigned)(pn + dn));
            free(buf);
            return -1;
        }
    }
    j.t0_us = j.last_progress_us = job_now_us();
    job_pl_split_reset();
    j.param = buf;
    j.param_bytes = pn;
    j.data = buf + pn;
    j.data_bytes = dn;
    (void)verbose;                     /* jobs are rare: always log them */
    printf("[job] kind %u flags 0x%X params %u data %u\n", (unsigned)j.kind, (unsigned)j.flags, (unsigned)pn,
           (unsigned)dn);
    if (job_crc32(buf, (size_t)pn + dn) != crc) {
        rc = job_fail(&j, "upload CRC-32 mismatch");
    } else if (j.kind == JOB_CAPS) {
        rc = job_caps(&j);
    } else if (j.kind == JOB_ECHO_EF) {
        rc = job_echo_run(&j);
    } else if (j.kind == JOB_SIMPSON_EF) {
        rc = job_simpson_run(&j);
    } else if (j.kind == JOB_STRAIN) {
        rc = job_strain_run(&j);
    } else if (j.kind == JOB_CT_SEG || j.kind == JOB_REG_SEARCH || j.kind == JOB_RENDER3D || j.kind == JOB_OVERLAY ||
               j.kind == JOB_TILE) {
        rc = job_fail(&j, "job not in this build (v5.27: heart + fundus vessels only)");
    } else {
        rc = job_fail(&j, "unknown job");
    }
    free(buf);
    status = (rc == 0 && !j.failed) ? 0u : 1u;
    proto_put_le32(end, status);
    if (send_msg(&j, JOB_MSG_END, end, 4u, NULL, 0u) != 0) {
        return -1;
    }
    printf("[job] kind %u done in %.1f ms (%u messages)\n", (unsigned)j.kind,
           (double)(job_now_us() - j.t0_us) / 1000.0, (unsigned)j.seq);
    if (frames_run != NULL) {
        (*frames_run)++;
    }
    return j.failed ? -1 : 0;
}
