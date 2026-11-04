/* unet_ai.h -- PS-side whole-network driver for the PLD U-Net accelerators.
 *
 * The tensors live entirely in the PL's 128 KB tensor BRAM (0xB000_0000); the PS
 * pushes each CONV node's parameters and runs the node, exactly like the Python
 * bring-up driver (ai/net/unet_board.py) but in C so it can live inside the TCP
 * server without spawning a process.
 *
 * The network description (node table + packed parameters) is generated into
 * src/unet_net.h / src/unet_net_data.c by ai/net/gen_unet_c.py from a
 * network.txt + params/ vector set.  Swapping those two files swaps the model;
 * no RTL and no code change is needed.
 */
#ifndef UNET_AI_H
#define UNET_AI_H

#include <stdint.h>

#include "unet_net.h"
#include "unet_models.h"

/** @brief Last-run statistics (filled by unet_run, may be NULL). */
typedef struct {
    uint32_t run_us;       /* time spent driving the accelerators */
    uint32_t nodes_run;    /* nodes executed */
    int32_t  logit_min;    /* min/max logit over the output layer */
    int32_t  logit_max;

    /* ---- step-0 profiling: where does run_us go? ----
     * ap/put/get/zero are all accumulated *inside* run_us, so
     *   run_us - (ap + put + get + zero)
     * is "other": PS-side loop bookkeeping, strip index math, register
     * pokes, fill_input_tensor and the final logits extraction. */
    uint32_t ap_us;        /* waiting for ap_done  = PL compute time */
    uint32_t put_us;       /* memcpy  PS memory -> tensor BRAM      */
    uint32_t get_us;       /* memcpy  tensor BRAM -> PS memory      */
    uint32_t zero_us;      /* hand-written zero fill of padding rows */
    uint32_t strips;       /* strips executed over the whole net    */
    uint64_t put_words;    /* 16-byte words written to BRAM (incl. params) */
    uint64_t get_words;    /* 16-byte words read from BRAM          */
    uint64_t param_words;  /* of put_words, how many were parameters */
} unet_stats_t;

/**
 * @brief Map the accelerator register files and the tensor BRAM.
 *
 * @param conv_ctrl  conv_layer AXI-Lite control base   (e.g. 0xA0010000)
 * @param elt_ctrl   eltwise_layer AXI-Lite control base(e.g. 0xA0020000)
 * @param bram_base  tensor BRAM window base            (e.g. 0xB0000000)
 * @return 0 ok; -1 mmap/open failed; -2 VERSION/ID check failed (PL not loaded?).
 */
int  unet_init(uint64_t conv_ctrl, uint64_t elt_ctrl, uint64_t bram_base);

void unet_close(void);

/** @brief Non-zero once unet_init() has succeeded. */
int  unet_ready(void);

/**
 * @brief The tensor BRAM as mapped by unet_init() (CPU pointer, physical base), for other PL kernels that
 * share it (pp_ai.c). Only touch it while no kernel runs; write with memcpy (never memset, see unet_ai.c).
 */
uint8_t *unet_bram_ptr(void);
uint64_t unet_bram_phys(void);

/**
 * @brief Pick the working resolution: every node dimension is divided by 2^shift.
 *
 * The U-Net's spatial dims are all 512 divided by a power of two, so shift
 * 0..4 gives 512 / 256 / 128 / 64 / 32.  shift=0 is full resolution (slowest,
 * most accurate); shift=4 is the 32x32 bring-up setting.  Tensors are allocated
 * once at full size, so switching is free.
 *
 * @return 0 ok; -1 bad shift or not initialised.
 */
int  unet_set_shift(int shift);
int  unet_get_shift(void);

/* ROI 局部推理的裁剪窗口约束(见 ai/net/roi_exactness.py 的证明):
 *   * 尺寸是 8 的倍数: 两级 2x2 池化后最深一层宽高仍为偶数;
 *   * 起点是 4 的倍数: 池化网格与整图对齐, 否则连中心区都会和整图不同;
 *   * 每边外扩 UNET_ROI_MARGIN: 裁剪边界的零填充最多影响 22 像素深,
 *     留 24 像素后 ROI 内的输出与整图推理逐值一致(贴着图像边的那侧不用留)。 */
#define UNET_SIZE_ALIGN   8u
#define UNET_ROI_ALIGN    4u
#define UNET_ROI_MARGIN   24u

/**
 * @brief Run the next unet_run() on an input of h x w pixels (a crop of the
 *        512x512 frame for ROI inference).  Every node dimension is scaled by
 *        h/512 and w/512; the strip scheduler and both HLS cores take any such
 *        size.  unet_set_shift() is the square special case and overrides this.
 *
 * @return 0 ok; -1 if h/w are not multiples of UNET_SIZE_ALIGN in [8, 512],
 *         or the accelerator is not initialised.
 */
int  unet_set_size(int h, int w);

/**
 * @brief Pick the parameter set used by the next unet_run() (src/unet_models.h).
 *
 * All models share the 30-node structure, so switching only swaps the node
 * table / packed parameters that are streamed into BRAM layer by layer --
 * no reallocation, no PL reconfiguration.  Model 0 is the fundus network.
 *
 * @return 0 ok; -1 index out of range (the current model is kept).
 */
int  unet_select_model(int idx);

/**
 * @brief Use a parameter set that is not compiled in (e.g. loaded from a file).
 *
 * The model must have the same 30-node structure as unet_models[0] (kind,
 * inputs, mode, channels, sizes); only quantisation fields and weights may
 * differ.  The caller keeps *m and the arrays it points to alive while it is
 * in use, and is responsible for param_off/param_len staying inside params.
 * Afterwards unet_get_model() returns -1.
 *
 * @return 0 ok; -1 NULL pointer or structure mismatch (the current model is kept).
 */
int  unet_use_model(const unet_model_t *m);

/** @brief Index of the current built-in model, or -1 after unet_use_model(). */
int  unet_get_model(void);

/**
 * @brief Run the whole network on one RGB image.
 *
 * @param rgb     h * w * 3 bytes for the current input size (512x512 by default,
 *                see unet_set_shift / unet_set_size), row-major RGB (the channel
 *                layout the model was trained on; values 0..255).
 * @param logits  [out] h * w int8 logits (channel 0).
 * @param st      [out] optional statistics.
 * @return 0 on success; negative on a failed node or a stuck accelerator.
 */
int  unet_run(const uint8_t *rgb, int8_t *logits, unet_stats_t *st);

#endif /* UNET_AI_H */
