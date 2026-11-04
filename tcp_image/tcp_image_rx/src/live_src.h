/* live_src.h -- live echo signal sources (10-25): where the frames of echo_ef mode 3 come from.
 *
 *   file   /root/echo_src/<name>.y8, played at its own frame rate: frame i is "captured" at t0 + i / fps (the capture
 *          thread sleeps until then). Stand-in for the capture card while it has not arrived, and the repeatable
 *          source for the acceptance runs. Format (little endian): "Y8V1", u32 W, u32 H, u32 frames, f64 fps,
 *          u32 0, u32 0, then frames x H x W bytes of luma.
 *   v4l2   /dev/videoN (UVC capture card), YUYV, mmap streaming; the luma is every 2nd byte (step 2), read in place
 *          from the driver's buffer. Timestamp = when the dequeue returned. NOT TESTED until the card arrives.
 * Both hand out a pointer to the frame's luma (stride, step) that stays valid until the next ls_next / ls_close.
 */
#ifndef LIVE_SRC_H
#define LIVE_SRC_H

#include <stdint.h>

enum { LS_FILE = 0, LS_V4L2 = 1 };

typedef struct {
    int       kind, W, H, stride, step, n;  /* n: frames in a file (0 = endless) */
    double    fps;
    int       fd, idx;
    uint64_t  t0_us;
    uint8_t  *buf;                          /* file: one frame */
    void     *mm[4];                        /* v4l2: mmapped buffers */
    uint32_t  mm_len[4];
    int       nbuf, cur;                    /* v4l2: buffer handed out (-1 none) */
} ls_t;

/** Open a source: kind LS_FILE with `name` = file name in /root/echo_src (no '/'), or LS_V4L2 with name =
 *  "videoN" and the wanted W, H, fps (the driver may change them; read them back from s). 0 ok, else -1 + msg. */
int ls_open(ls_t *s, int kind, const char *name, int W, int H, double fps, char *err, int errlen);

/** Next frame (blocks until it is due / captured). 0 ok, 1 end of file, -1 error. *t_us = capture time. */
int ls_next(ls_t *s, const uint8_t **luma, uint64_t *t_us);

void ls_close(ls_t *s);

#endif /* LIVE_SRC_H */
