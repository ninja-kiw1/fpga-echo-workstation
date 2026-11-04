# 案例 02：服务端 v5.16 首次部署段错误

## 背景

10-14 至 10-16 做 P1：新比特流 r2（PL 配准引擎 + 图形叠加）和配套的板端服务 v5.16，另有板上三维重建（CT）等功能。
r2 先单独上板验收通过，然后部署 v5.16。

## 提示词

本轮开始时的任务（对话续接摘要中记录的用户要求，原文英文，节选）：

> Initial request: carry out P1 (1.1–1.7) of `待办优先级_2026-10-14.md`, following the prompt `新对话提示词_P1_2026-10-14.md` ...
> Bitstream r2 `xu2dp_e2b_pp_r2` (control at 0xA004_0000, data on ai_data_ic); server v5.16 with fallbacks; UI shows which path ran.

部署过程中用户的指示：

> 左房照你的建议做，继续吧

## 模型回答与纠错轨迹

**1. 验收脚本发现崩溃，模型直接定位到代码**（10-16，原文）：

> Segfault (NULL pointer in libc) on `ct_seg` with flags 0x44 (MASK_FINAL | RENDER3D), params 12. Looking at the render3d branch of `job_ct.c`:

> The bug: `job_ct` passes `views=12` with `angles = NULL`, and `job_render3d` then does `memcpy(ang, NULL, …)`. The fix is to space the views evenly when no angles are given:

**2. 先回滚，再修复，再重新部署，保持回滚链干净**（原文）：

> **First deploy crashed:** the CT + 3D rendering job hit a NULL pointer. I rolled back to v5.15, fixed it, and redeployed, so the rollback chain is clean.

这里的关键是顺序：没有在出错的版本上"就地打补丁"，而是先回到已知正常的 v5.15，修好后作为新的 v5.16 重新走一遍完整部署，
这样板上任何时刻都只有"上一版正常"和"这一版已验收"两种状态。

**3. 如实报告未完成的部分**（原文）：

> r2 and v5.16 are both deployed on the board and passed every check I ran on them. The full regression against v5.16 is still running in the background, so v5.16 isn't fully signed off yet.

## 结果与证据

- v5.16 修复后通过新功能 6 项检查和 18 组回归；PL 配准引擎三步搜索 1,428 ms → 1.26 ms，与旧路径结果完全相同。
- 证据：交接 §9.61–9.63（`tcp_image/tcp_image_rx/progress_and_handoff.md`）。

## 沉淀

`skill/board_deploy_rollback.md`（"先回滚再修复"写进了流程）、`skill/bit_exact_verification.md`（新功能必须有自动检查）。
