# 踩坑清单（Zynq UltraScale+ 板级开发）

每条按"触发条件 → 现象 → 排查 / 解决"整理，都是本作品开发中真实遇到并确认过原因的问题。

| # | 触发条件 | 现象 | 排查 / 解决 |
| ---: | --- | --- | --- |
| 1 | 开发机开着 TUN 模式代理 | 板子没接网线时 ping 也通，22 / 5060 端口"开着" | 看 SSH 是否返回 `SSH-2.0-` 横幅；让代理绕过 192.168.1.0/24；确认本机有线网卡地址 |
| 2 | 板上没有 RTC，系统时间每次开机回到旧日期 | `journalctl --since` 查不到刚发生的日志，旧日志排在新日志后面 | 用 `journalctl -b` 按本次启动查，不按时间查 |
| 3 | 服务用 `Nice=-20`，但压力进程由 ssh 启动 | 加负载后服务延迟仍翻倍 | cgroup v1 下 system.slice 和 user.slice 份额相同，nice 只在同一 slice 内有效；给服务单独建 slice 并提高 CPUShares |
| 4 | 板子与其他项目共用，SD 卡 boot 分区被换了内核 | PL 驱动模块加载失败，`/proc/iomem` 里 PL 用的 DDR 变成 System RAM | 先查 `uname -r` 和 `/proc/iomem`；换回原内核镜像，别的内核改名保存 |
| 5 | HLS 核正在运行时重新加载 PL | 板子卡死，只能断电 | 换比特流前先停掉所有用 PL 的服务；比特流只在开机时由 systemd 服务加载 |
| 6 | 驱动访问了新比特流里已删除的 AXI 外设地址 | PS 直接挂死 | 驱动按比特流 sha256 指纹清单决定是否启用该外设；删外设时同步更新清单 |
| 7 | JTAG 读写大块内存 | 随机错字、`Invalid DAP IDCODE/ACK`，降到 5 MHz 也有 | 分块传输 + 每块校验 + 进程级重试；XSCT 丢失调试上下文后只能重开进程 |
| 8 | HLS 重新综合前 `solution1/impl` 里有旧结果 | 导出的 IP 实际是旧 RTL | `evaluate` 前删掉 `solution1/impl` |
| 9 | 同时跑两个 HLS cosim / 两个 Vivado 完整构建 | 内存耗尽（eltwise cosim 约 17 GB）或 tclapp 初始化冲突 | 一次只跑一个 |
| 10 | 两个依次消费的 m_axi 读端口 | 板上吞吐低于预期，cosim 看不出 | 设 `num_read_outstanding=2`；以板上实测为准 |
| 11 | DP 显示器休眠时板子启动 | `failed to train the DP link`，桌面不显示 | 开机前唤醒显示器；或 `systemctl restart lxdm` 后用 `xrandr` 设 1920×1080 |
| 12 | Vitis 导出的文件带只读属性，再被脚本复制 | 第二次清理目录时 `PermissionError` | 删除前先清除只读属性（`os.chmod`） |

## 适用场景

Zynq / Zynq UltraScale+ 板 + PetaLinux / Ubuntu 根文件系统 + Vivado / Vitis HLS 的开发与上板调试。

## 使用方法

遇到"板子不响应 / 结果对不上 / 性能突然变差"时，按表逐条排除环境原因，再怀疑代码。
第 1、2、4 条是最常见的"看起来是代码问题，其实是环境问题"。

## 失效条件

与具体板卡外设相关的条目（第 4、11 条）换板后可能不适用；第 3 条只适用于 cgroup v1 的系统。

## 已验证效果 / 来源

每一条都至少耗费过一轮完整排查才定位到原因；整理成表以后，同类问题再次出现时都在几分钟内定位。
详细经过见 `tcp_image/tcp_image_rx/progress_and_handoff.md` 的"踩坑"章节。
