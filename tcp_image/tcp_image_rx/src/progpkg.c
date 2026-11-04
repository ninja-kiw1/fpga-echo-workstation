/* progpkg.c -- program packages loaded at run time (see progpkg.h). */
#include "progpkg.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "unet_ai.h"

#define PKG_MAGIC        "UPK1"
#define PKG_VERSION      1u
#define PKG_HEADER_BYTES 64u
#define PKG_NODE_BYTES   32u
#define PKG_NAME_BYTES   32u
#define PKG_MAX_FILE     (8u << 20)

typedef struct {
    unet_model_t m;
    unet_node_t  nodes[UNET_NODES_LEN];
    uint32_t     param_off[UNET_NODES_LEN];
    uint32_t     param_len[UNET_NODES_LEN];
    uint8_t     *params;                       /* 16-byte aligned: copied into BRAM 32 bits at a time */
    char         name[PKG_NAME_BYTES + 1u];
    uint32_t     crc;
} prog_t;

static prog_t *g_prog[PROG_MAX_ID + 1u];

/* ------------------------------------------------------------------ */

uint32_t prog_crc32(const uint8_t *p, size_t n)
{
    static uint32_t table[256];
    static int      ready;
    uint32_t        c = 0xFFFFFFFFu;
    size_t          i;

    if (!ready) {
        uint32_t k, b, v;

        for (k = 0u; k < 256u; k++) {
            v = k;
            for (b = 0u; b < 8u; b++) {
                v = (v & 1u) ? (0xEDB88320u ^ (v >> 1)) : (v >> 1);
            }
            table[k] = v;
        }
        ready = 1;
    }
    for (i = 0u; i < n; i++) {
        c = table[(c ^ p[i]) & 0xFFu] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFu;
}

static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

const unet_model_t *prog_model(unsigned id)
{
    if (id < UNET_MODELS_COUNT) {
        return UNET_MODEL_PRESENT(id) ? &unet_models[id] : NULL;    /* v5.27: removed index = no program */
    }
    if (id <= PROG_MAX_ID && g_prog[id] != NULL) {
        return &g_prog[id]->m;
    }
    return NULL;
}

int prog_select(unsigned id)
{
    if (id < UNET_MODELS_COUNT) {
        return unet_select_model((int)id);
    }
    if (id <= PROG_MAX_ID && g_prog[id] != NULL) {
        return unet_use_model(&g_prog[id]->m);
    }
    return -1;
}

uint32_t prog_crc(unsigned id)
{
    return (id > PROG_MAX_ID || id < UNET_MODELS_COUNT || g_prog[id] == NULL) ? 0u : g_prog[id]->crc;
}

static void prog_free(prog_t *p)
{
    if (p != NULL) {
        free(p->params);
        free(p);
    }
}

static uint8_t *read_file(const char *path, size_t *len)
{
    FILE    *f = fopen(path, "rb");
    uint8_t *buf = NULL;
    long     n;

    if (f == NULL) {
        return NULL;
    }
    if (fseek(f, 0, SEEK_END) == 0 && (n = ftell(f)) > 0 && (unsigned long)n <= PKG_MAX_FILE &&
        fseek(f, 0, SEEK_SET) == 0 && (buf = (uint8_t *)malloc((size_t)n)) != NULL) {
        if (fread(buf, 1u, (size_t)n, f) != (size_t)n) {
            free(buf);
            buf = NULL;
        } else {
            *len = (size_t)n;
        }
    }
    fclose(f);
    return buf;
}

/* 解析 + 校验 + 自检。成功返回 prog_t, 失败返回 NULL 并在 why 里写原因。 */
static prog_t *load_one(const uint8_t *b, size_t len, char *why, size_t why_len, uint32_t *diff_out)
{
    const uint8_t *q;
    prog_t        *p;
    uint32_t       params_bytes, meta_bytes, body_bytes, crc, side, npx, i, need;
    unsigned       id, sts, k;
    int8_t        *out = NULL;
    unet_stats_t   ust;
    int            rc;

    *diff_out = 0u;
    if (len < PKG_HEADER_BYTES || memcmp(b, PKG_MAGIC, 4u) != 0) {
        snprintf(why, why_len, "not a UPK1 package");
        return NULL;
    }
    if (le16(b + 4) != PKG_VERSION || le16(b + 6) != PKG_HEADER_BYTES) {
        snprintf(why, why_len, "unsupported version %u", (unsigned)le16(b + 4));
        return NULL;
    }
    id = b[8];
    sts = b[13];
    params_bytes = le32(b + 16);
    meta_bytes = le32(b + 20);
    body_bytes = le32(b + 24);
    crc = le32(b + 28);
    if (body_bytes != len - PKG_HEADER_BYTES) {
        snprintf(why, why_len, "length %u, header says %u", (unsigned)(len - PKG_HEADER_BYTES), (unsigned)body_bytes);
        return NULL;
    }
    if (prog_crc32(b + PKG_HEADER_BYTES, body_bytes) != crc) {
        snprintf(why, why_len, "CRC-32 mismatch");
        return NULL;
    }
    if (id < UNET_MODELS_COUNT || id > PROG_MAX_ID) {
        snprintf(why, why_len, "id %u outside %u..%u", id, (unsigned)UNET_MODELS_COUNT, (unsigned)PROG_MAX_ID);
        return NULL;
    }
    if (g_prog[id] != NULL) {
        snprintf(why, why_len, "id %u already taken by %s", id, g_prog[id]->name);
        return NULL;
    }
    if (b[12] != UNET_NODES_LEN || sts > 4u || b[9] > 4u || b[11] > 1u) {
        snprintf(why, why_len, "bad header fields");
        return NULL;
    }
    side = 512u >> sts;
    npx = side * side;
    need = UNET_NODES_LEN * (PKG_NODE_BYTES + 8u) + params_bytes + meta_bytes + npx * 4u;
    if (need != body_bytes) {
        snprintf(why, why_len, "section sizes do not add up");
        return NULL;
    }
    p = (prog_t *)calloc(1u, sizeof(*p));
    if (p == NULL || posix_memalign((void **)&p->params, 16u, params_bytes > 0u ? params_bytes : 16u) != 0) {
        free(p);
        snprintf(why, why_len, "out of memory");
        return NULL;
    }
    memcpy(p->name, b + 32, PKG_NAME_BYTES);
    p->name[PKG_NAME_BYTES] = '\0';
    for (k = 1u; k <= PROG_MAX_ID; k++) {
        const unet_model_t *o = prog_model(k);

        if (o != NULL && strcmp(o->name, p->name) == 0) {
            snprintf(why, why_len, "name \"%s\" already used by model %u", p->name, k);
            prog_free(p);
            return NULL;
        }
    }
    if (strcmp(unet_models[0].name, p->name) == 0 || p->name[0] == '\0') {
        snprintf(why, why_len, "bad name");
        prog_free(p);
        return NULL;
    }

    q = b + PKG_HEADER_BYTES;
    for (i = 0u; i < UNET_NODES_LEN; i++, q += PKG_NODE_BYTES) {
        unet_node_t *n = &p->nodes[i];

        n->kind = q[0];
        n->in0 = (int8_t)q[1];
        n->in1 = (int8_t)q[2];
        n->mode = q[3];
        n->ca = q[4];
        n->cb = q[5];
        n->out_c = q[6];
        n->relu = q[7];
        n->shift = q[8];
        n->qmin = (int16_t)le16(q + 12);
        n->qmax = (int16_t)le16(q + 14);
        n->m0 = (int32_t)le32(q + 16);
        n->m1 = (int32_t)le32(q + 20);
        n->in_h = le16(q + 24);
        n->in_w = le16(q + 26);
        n->out_h = le16(q + 28);
        n->out_w = le16(q + 30);
    }
    for (i = 0u; i < UNET_NODES_LEN; i++) {
        p->param_off[i] = le32(q + 4u * i);
        p->param_len[i] = le32(q + 4u * (UNET_NODES_LEN + i));
        /* 参数长度必须和内置模型一样(结构相同 => 每层参数字节数相同), 偏移不能越界 */
        if (p->param_len[i] != unet_models[0].param_len[i] ||
            p->param_off[i] > params_bytes || p->param_len[i] > params_bytes - p->param_off[i]) {
            snprintf(why, why_len, "node %u parameters out of range", (unsigned)i);
            prog_free(p);
            return NULL;
        }
    }
    q += 8u * UNET_NODES_LEN;
    memcpy(p->params, q, params_bytes);
    q += params_bytes + meta_bytes;

    p->m.name = p->name;
    p->m.nodes = p->nodes;
    p->m.param_off = p->param_off;
    p->m.param_len = p->param_len;
    p->m.params = p->params;
    p->m.default_shift = b[9];
    p->m.default_thr = (int8_t)b[10];
    p->m.roi_ok = b[11];
    p->crc = crc;

    /* 自检: 在 PL 上跑包里的测试输入, logit 必须逐位等于包里的期望值 */
    out = (int8_t *)malloc(npx);
    if (out == NULL) {
        snprintf(why, why_len, "out of memory");
        prog_free(p);
        return NULL;
    }
    if (unet_use_model(&p->m) != 0) {
        snprintf(why, why_len, "structure differs from the built-in models");
        rc = -1;
    } else if (unet_set_shift((int)sts) != 0 || unet_run(q, out, &ust) != 0) {
        snprintf(why, why_len, "self-test run failed");
        rc = -1;
    } else {
        const int8_t *exp = (const int8_t *)(q + npx * 3u);

        for (i = 0u; i < npx; i++) {
            *diff_out += (out[i] != exp[i]) ? 1u : 0u;
        }
        rc = (*diff_out == 0u) ? 0 : -1;
        snprintf(why, why_len, "self-test %ux%u: %u/%u logits differ, ai %u us",
                 (unsigned)side, (unsigned)side, (unsigned)*diff_out, (unsigned)npx, (unsigned)ust.run_us);
    }
    free(out);
    if (rc != 0) {
        prog_free(p);
        return NULL;
    }
    return p;
}

static int upk_filter(const struct dirent *e)
{
    size_t n = strlen(e->d_name);

    return n > 4u && strcmp(e->d_name + n - 4u, ".upk") == 0;
}

int prog_load_dir(const char *dir)
{
    struct dirent **list = NULL;
    char            path[512], why[160];
    FILE           *log;
    int             n, i, loaded = 0, shift = unet_get_shift();
    unsigned        k;

    for (k = 0u; k <= PROG_MAX_ID; k++) {
        prog_free(g_prog[k]);
        g_prog[k] = NULL;
    }
    (void)unet_select_model(0);
    if (unet_ready() == 0) {
        printf("[prog] U-Net not initialised; no program packages loaded\n");
        return 0;
    }
    n = scandir(dir, &list, upk_filter, alphasort);
    if (n < 0) {
        printf("[prog] %s: no program directory, built-in models only\n", dir);
        return 0;
    }
    /* 先写 .tmp, 全部自检完再改名: 读到 LOADED.txt 的人看到的一定是完整结果 */
    snprintf(path, sizeof(path), "%s/LOADED.txt.tmp", dir);
    log = fopen(path, "w");
    if (log != NULL) {
        fprintf(log, "# file\tid\tname\tcrc32\tresult\n");
    }
    for (i = 0; i < n; i++) {
        uint8_t *buf;
        size_t   len = 0u;
        prog_t  *p = NULL;
        uint32_t diff = 0u;

        snprintf(path, sizeof(path), "%s/%s", dir, list[i]->d_name);
        buf = read_file(path, &len);
        if (buf == NULL) {
            snprintf(why, sizeof(why), "cannot read (or larger than %u bytes)", (unsigned)PKG_MAX_FILE);
        } else {
            p = load_one(buf, len, why, sizeof(why), &diff);
        }
        if (p != NULL) {
            unsigned id = buf[8];

            g_prog[id] = p;
            loaded++;
            printf("[prog] %-24s id %3u %-16s crc %08X  OK (%s)\n", list[i]->d_name, id, p->name,
                   (unsigned)p->crc, why);
            if (log != NULL) {
                fprintf(log, "%s\t%u\t%s\t%08X\tOK %s\n", list[i]->d_name, id, p->name, (unsigned)p->crc, why);
            }
        } else {
            printf("[prog] %-24s REJECTED: %s\n", list[i]->d_name, why);
            if (log != NULL) {
                fprintf(log, "%s\t-\t-\t-\tREJECTED %s\n", list[i]->d_name, why);
            }
        }
        free(buf);
        free(list[i]);
    }
    free(list);
    if (log != NULL) {
        char final[512];

        fclose(log);
        snprintf(path, sizeof(path), "%s/LOADED.txt.tmp", dir);
        snprintf(final, sizeof(final), "%s/LOADED.txt", dir);
        if (rename(path, final) != 0) {
            printf("[prog] cannot write %s\n", final);
        }
    }
    (void)unet_select_model(0);
    (void)unet_set_shift(shift);
    printf("[prog] %d program package(s) loaded from %s\n", loaded, dir);
    return loaded;
}
