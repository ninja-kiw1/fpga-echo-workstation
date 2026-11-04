/* job.h -- case jobs (control header v7 "HM"): one request runs a whole case on the board.
 *
 * A job uploads a series once (a CT volume file, two frames, ...), runs every step on the board
 * (input encoding on the PS, U-Net programs on the PL, post-processing on the PL / PS, decisions on
 * the PS) and returns numbers + masks. The PC only picks files and displays. Plan: E:/PLD/文档/规划与构想/板端化规划_2026-10-12.md.
 *
 * Request (little endian), after which the server reads params and data:
 *   off  0 u16 magic 0x4D48 ("HM")      off  2 u8 job (JOB_*)     off  3 u8 version (1)
 *   off  4 u32 flags (per job)           off  8 u32 param bytes    off 12 u32 data bytes
 *   off 16 u32 CRC-32 (zlib) of params followed by data            off 20 u32 0
 * Reply: a sequence of messages, each a 16-byte header + payload:
 *   u16 magic 0x524A ("JR"), u8 type (JOB_MSG_*), u8 0, u32 payload bytes, u32 CRC-32 of the payload, u32 seq
 *   PROGRESS  JSON {"stage":..,"done":..,"total":..}; at least every JOB_PROGRESS_MS while the job runs
 *             (the PC waits with a socket timeout, so a long job must keep talking)
 *   BLOB      u8 name length, name, u8 encoding (0 raw, 1 zlib), u32 raw bytes, data
 *   RESULT    JSON, "ok": true / false (+ "error")
 *   END       u32 status (0 ok); always the last message of a job
 * The connection stays open after END (next request may be a job or a frame).
 */
#ifndef JOB_H
#define JOB_H

#include <stddef.h>
#include <stdint.h>

#include "unet_ai.h"

#define CTRL_MAGIC_JOB    0x4D48u     /* little-endian "HM" */
#define JOB_HDR_BYTES     24u
#define JOB_VERSION       1u
#define JOB_MSG_MAGIC     0x524Au     /* little-endian "JR" */
#define JOB_MSG_HDR_BYTES 16u
#define JOB_MAX_DATA      (320u << 20)   /* largest upload (a 168-slice float32 CT is ~ 60 MB gzipped) */
#define JOB_PROGRESS_MS   500u

enum { JOB_CAPS = 0, JOB_CT_SEG = 1, JOB_REG_SEARCH = 2, JOB_ECHO_EF = 3, JOB_SIMPSON_EF = 4, JOB_RENDER3D = 5,
       JOB_OVERLAY = 6, JOB_STRAIN = 7, JOB_TILE = 8 };
enum { JOB_MSG_PROGRESS = 1, JOB_MSG_BLOB = 2, JOB_MSG_RESULT = 3, JOB_MSG_END = 4 };

typedef struct job {
    int            fd;
    uint32_t       kind, flags;
    const uint8_t *param;
    uint32_t       param_bytes;
    const uint8_t *data;
    uint32_t       data_bytes;
    uint32_t       seq;
    uint64_t       t0_us, last_progress_us;
    int            failed;          /* a send failed: stop the job, drop the connection */
} job_t;

/* JSON builder: appends with snprintf into a fixed buffer, keeps it terminated. */
typedef struct {
    char  *s;
    size_t cap, len;
} jbuf_t;

void jb_init(jbuf_t *b, char *mem, size_t cap);
void jb_add(jbuf_t *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

uint64_t job_now_us(void);

/** @brief Progress message, sent only if JOB_PROGRESS_MS passed since the last one (force = always).
 *  @return 0 ok, -1 the connection is gone (job must stop). */
int job_progress(job_t *j, const char *stage, uint32_t done, uint32_t total, int force);

/** @brief Progress message with a ready-made JSON object (always sent). */
int job_progress_json(job_t *j, const char *json);

/** @brief Named binary result; zlib = 1 compresses it (level 1) when that makes it smaller. */
int job_blob(job_t *j, const char *name, const void *p, size_t n, int zlib);

/** @brief The job's JSON result (exactly one per job, before END). */
int job_result(job_t *j, const char *json);

/** @brief Error result: {"ok":false,"error":msg}. */
int job_fail(job_t *j, const char *msg);

/**
 * @brief Read the rest of a job request (the 2 magic bytes are already read), run it, send END.
 * @return 0 keep the connection, -1 drop it.
 */
int job_handle(int cfd, const uint8_t *first2, uint32_t *frames_run, int verbose);

/* jobs (job_echo.c, job_simpson.c, job_strain.c); each returns 0 when it sent a RESULT (ok or not), -1 when the
 * link failed. v5.27 (heart-only build) removed ct_seg / reg_search / render3d / overlay / tile: their kind numbers
 * stay reserved and answer "not in this build". */
int job_echo_run(job_t *j);
int job_simpson_run(job_t *j);
int job_strain_run(job_t *j);

/* shared helpers */
uint32_t job_crc32(const uint8_t *p, size_t n);
/** @brief numpy's add.reduce on a contiguous float64 array (pairwise summation), bit for bit. */
double job_np_sum(const double *a, int n);
/** @brief Run built-in / installed program `model` on a (512>>shift)^2 RGB input. 0 ok. */
int job_unet(unsigned model, int shift, const uint8_t *rgb, int8_t *logits, uint32_t *run_us);
/** @brief v5.16: append ,"pl_split":{ap/put/get/zero/other ms} of this job's U-Net runs (software timers). */
void job_pl_split_json(jbuf_t *b);
void job_pl_split_reset(void);
/** @brief v5.20: add one unet_run's software timers to this job's pl_split (for jobs calling unet_run directly). */
void job_unet_account(const unet_stats_t *st);

#endif /* JOB_H */
