/* live_src.c -- see live_src.h. */
#include "live_src.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "job.h"

#define SRC_DIR "/root/echo_src/"

static int read_all(int fd, void *p, size_t n)
{
    uint8_t *q = (uint8_t *)p;

    while (n > 0u) {
        ssize_t k = read(fd, q, n);

        if (k <= 0) {
            return -1;
        }
        q += k;
        n -= (size_t)k;
    }
    return 0;
}

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

static int xioctl(int fd, unsigned long req, void *arg)
{
    int r;

    do {
        r = ioctl(fd, req, arg);
    } while (r < 0 && errno == EINTR);
    return r;
}

static int open_file(ls_t *s, const char *name, char *err, int errlen)
{
    char    path[160];
    uint8_t h[32];
    double  fps;

    if (name[0] == '\0' || strchr(name, '/') != NULL || strstr(name, "..") != NULL || strlen(name) > 120u) {
        snprintf(err, (size_t)errlen, "bad source file name");
        return -1;
    }
    snprintf(path, sizeof(path), SRC_DIR "%s", name);
    s->fd = open(path, O_RDONLY);
    if (s->fd < 0 || read_all(s->fd, h, sizeof(h)) != 0 || memcmp(h, "Y8V1", 4) != 0) {
        snprintf(err, (size_t)errlen, "cannot open %s as a Y8V1 file", path);
        return -1;
    }
    s->W = (int)le32(h + 4);
    s->H = (int)le32(h + 8);
    s->n = (int)le32(h + 12);
    memcpy(&fps, h + 16, 8u);
    s->fps = fps;
    if (s->W < 112 || s->H < 112 || s->W > 4096 || s->H > 4096 || s->n < 1 || !(fps > 0.0 && fps < 1000.0)) {
        snprintf(err, (size_t)errlen, "bad Y8V1 header");
        return -1;
    }
    s->stride = s->W;
    s->step = 1;
    s->buf = (uint8_t *)malloc((size_t)s->W * s->H);
    if (s->buf == NULL) {
        snprintf(err, (size_t)errlen, "out of memory");
        return -1;
    }
    return 0;
}

static int open_v4l2(ls_t *s, const char *name, int W, int H, double fps, char *err, int errlen)
{
    char                       path[64];
    struct v4l2_format         f;
    struct v4l2_streamparm     sp;
    struct v4l2_requestbuffers rb;
    enum v4l2_buf_type         ty = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    int                        i;

    if (strncmp(name, "video", 5) != 0 || strchr(name, '/') != NULL || strlen(name) > 12u) {
        snprintf(err, (size_t)errlen, "v4l2 source must be videoN");
        return -1;
    }
    snprintf(path, sizeof(path), "/dev/%s", name);
    s->fd = open(path, O_RDWR);
    if (s->fd < 0) {
        snprintf(err, (size_t)errlen, "cannot open %s", path);
        return -1;
    }
    memset(&f, 0, sizeof(f));
    f.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    f.fmt.pix.width = (uint32_t)W;
    f.fmt.pix.height = (uint32_t)H;
    f.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
    f.fmt.pix.field = V4L2_FIELD_NONE;
    if (xioctl(s->fd, VIDIOC_S_FMT, &f) < 0 || f.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV) {
        snprintf(err, (size_t)errlen, "%s: YUYV %dx%d not accepted", path, W, H);
        return -1;
    }
    s->W = (int)f.fmt.pix.width;
    s->H = (int)f.fmt.pix.height;
    s->stride = (int)f.fmt.pix.bytesperline;
    s->step = 2;
    memset(&sp, 0, sizeof(sp));
    sp.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    sp.parm.capture.timeperframe.numerator = 1000u;
    sp.parm.capture.timeperframe.denominator = (uint32_t)(fps * 1000.0 + 0.5);
    s->fps = fps;
    if (xioctl(s->fd, VIDIOC_S_PARM, &sp) == 0 && sp.parm.capture.timeperframe.numerator > 0u) {
        s->fps = (double)sp.parm.capture.timeperframe.denominator / sp.parm.capture.timeperframe.numerator;
    }
    memset(&rb, 0, sizeof(rb));
    rb.count = 4u;
    rb.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    rb.memory = V4L2_MEMORY_MMAP;
    if (xioctl(s->fd, VIDIOC_REQBUFS, &rb) < 0 || rb.count < 2u) {
        snprintf(err, (size_t)errlen, "%s: no mmap buffers", path);
        return -1;
    }
    s->nbuf = (int)(rb.count < 4u ? rb.count : 4u);
    for (i = 0; i < s->nbuf; i++) {
        struct v4l2_buffer b;

        memset(&b, 0, sizeof(b));
        b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        b.memory = V4L2_MEMORY_MMAP;
        b.index = (uint32_t)i;
        if (xioctl(s->fd, VIDIOC_QUERYBUF, &b) < 0) {
            snprintf(err, (size_t)errlen, "%s: QUERYBUF failed", path);
            return -1;
        }
        s->mm_len[i] = b.length;
        s->mm[i] = mmap(NULL, b.length, PROT_READ | PROT_WRITE, MAP_SHARED, s->fd, b.m.offset);
        if (s->mm[i] == MAP_FAILED) {
            s->mm[i] = NULL;
            snprintf(err, (size_t)errlen, "%s: mmap failed", path);
            return -1;
        }
        if (xioctl(s->fd, VIDIOC_QBUF, &b) < 0) {
            snprintf(err, (size_t)errlen, "%s: QBUF failed", path);
            return -1;
        }
    }
    if (xioctl(s->fd, VIDIOC_STREAMON, &ty) < 0) {
        snprintf(err, (size_t)errlen, "%s: STREAMON failed", path);
        return -1;
    }
    return 0;
}

int ls_open(ls_t *s, int kind, const char *name, int W, int H, double fps, char *err, int errlen)
{
    int rc;

    memset(s, 0, sizeof(*s));
    s->kind = kind;
    s->fd = -1;
    s->cur = -1;
    rc = kind == LS_FILE ? open_file(s, name, err, errlen) :
         kind == LS_V4L2 ? open_v4l2(s, name, W, H, fps, err, errlen) : -1;
    if (rc != 0) {
        if (kind != LS_FILE && kind != LS_V4L2) {
            snprintf(err, (size_t)errlen, "unknown source kind %d", kind);
        }
        ls_close(s);
        return -1;
    }
    s->t0_us = job_now_us();
    return 0;
}

int ls_next(ls_t *s, const uint8_t **luma, uint64_t *t_us)
{
    if (s->kind == LS_FILE) {
        uint64_t due = s->t0_us + (uint64_t)((double)s->idx * 1e6 / s->fps + 0.5), now = job_now_us();

        if (s->idx >= s->n) {
            return 1;
        }
        if (due > now) {
            usleep((useconds_t)(due - now));
        }
        *t_us = job_now_us();
        if (read_all(s->fd, s->buf, (size_t)s->W * s->H) != 0) {
            return -1;
        }
        s->idx++;
        *luma = s->buf;
        return 0;
    }
    {
        struct v4l2_buffer b;

        if (s->cur >= 0) {                           /* give the previous buffer back to the driver */
            memset(&b, 0, sizeof(b));
            b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
            b.memory = V4L2_MEMORY_MMAP;
            b.index = (uint32_t)s->cur;
            xioctl(s->fd, VIDIOC_QBUF, &b);
            s->cur = -1;
        }
        memset(&b, 0, sizeof(b));
        b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        b.memory = V4L2_MEMORY_MMAP;
        if (xioctl(s->fd, VIDIOC_DQBUF, &b) < 0) {
            return -1;
        }
        *t_us = job_now_us();
        s->cur = (int)b.index;
        s->idx++;
        *luma = (const uint8_t *)s->mm[b.index];
        return 0;
    }
}

void ls_close(ls_t *s)
{
    int i;

    if (s->kind == LS_V4L2 && s->fd >= 0) {
        enum v4l2_buf_type ty = V4L2_BUF_TYPE_VIDEO_CAPTURE;

        xioctl(s->fd, VIDIOC_STREAMOFF, &ty);
    }
    for (i = 0; i < 4; i++) {
        if (s->mm[i] != NULL) {
            munmap(s->mm[i], s->mm_len[i]);
            s->mm[i] = NULL;
        }
    }
    if (s->fd >= 0) {
        close(s->fd);
        s->fd = -1;
    }
    free(s->buf);
    s->buf = NULL;
}
