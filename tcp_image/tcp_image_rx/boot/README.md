# 固化到板子（开机自动恢复到可用状态）

> 2026-10-02。目标：**断电重启后，板子自动回到"PL 跑我们的设计 + 业务服务在跑"的状态**，
> 不需要人工敲任何命令。已实测（重启一次，端到端复验通过）。

## 1. 固化包含哪几件（都在 SD 卡根文件系统 `mmcblk1p2` 上，断电不丢）

| # | 东西 | 位置 | 谁负责开机拉起 |
| --- | --- | --- | --- |
| 1 | **PL 比特流** `ai_sys.bin` | `/lib/firmware/ai_sys.bin` | **`ai-sys-bitstream.service`**（本次新做） |
| 2 | 业务程序 `tcp_image_server` | `/root/tcp_image_rx/` | `tcp-image-server.service`（enabled） |
| 3 | `plbram.ko`（Normal-NC 映射） | `/lib/modules/<ver>/extra/plbram.ko` | `plbram.service`（enabled） |
| 4 | 内核里那个 `bc` 替身（只在编模块时用） | `/usr/local/bin/bc` | — |

**出厂状态里唯一缺的是第 1 件**：boot.bin 带的是**厂商比特流**，上电后 PL 是厂商设计，
业务程序会初始化失败、被 `Restart=always` 反复拉起（表现为服务一直 `activating`）。
本次补上了这一环。

## 2. 开机顺序（systemd）

```
local-fs → rc-local（厂商脚本，insmod fpgadrv，此时 PL 还是厂商设计）
         → ai-sys-bitstream（卸 fpgadrv + 把 ai_sys.bin 烧进 PL + 校验）
         → plbram（modprobe plbram → /dev/plbram）
         → tcp-image-server（业务程序）
```

* `ai-sys-bitstream.service`：`After=local-fs.target rc-local.service`、
  `Before=plbram.service tcp-image-server.service`。
  **让 rc-local 先跑**是有意的：厂商驱动只会对着厂商 PL 探测；随后我们把 PL 换掉并
  卸掉 `fpgadrv`，"最后说话的是我们"。
* 脚本 `/usr/local/sbin/load_ai_sys.sh` 是**幂等 + 失败不挡启动**的，
  并且会**读回 `0xA0000010` 校验**（我们的 `img_proc_axil` VERSION = `0x494D5003`），
  所以 journal 里能直接看到"烧进去的到底是不是我们的设计"。

## 3. 怎么验证

```sh
# 板上
systemctl status ai-sys-bitstream plbram tcp-image-server --no-pager
journalctl -b -u ai-sys-bitstream --no-pager        # 看那次开机烧录 + 校验
cat /sys/class/fpga_manager/fpga0/state             # 期望 operating
python3 -c "import mmap,os,struct;fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC);\
m=mmap.mmap(fd,0x1000,mmap.MAP_SHARED,mmap.PROT_READ|mmap.PROT_WRITE,offset=0xA0000000);\
print(hex(struct.unpack_from('<I',m,0x10)[0]))"      # 期望 0x494d5003

# PC 端
python tcp_image_ui/imagelink.py 192.168.1.254 5060 bars --flags mask,stats --ai-res 512
```

或直接从 PC 上跑：`python ai/net/_boot_install.py check|install|now|reboot`。

## 4. ⚠ 注意事项

* **刚开机的 1 分钟内别急着连**：板子在起桌面（4×A53，load ~1.2），首次连接可能超时，
  等一分钟就好 —— **不是程序的问题**（本次验收也是第一次超时、第二次成功）。
* **厂商的 `fpgadrv` 会被卸掉**，厂商那套 MIPI 摄像/串口功能随之失效 —— 这是换 PL 的
  必然代价，之前已经确认接受。
* **板子时钟停在 2021 年**（`Aug 31 19:53`）→ journal 时间戳不可信、`make` 会误判
  "无需重建"（要 `make clean`）。
* 我们的设计**只应答 `0xA0000000 / 0xA0010000 / 0xA0020000` 三个地址**（与厂商 PL 节点
  同址），所以即便厂商驱动对着我们的 PL 探测也不会挂（地址都有从机）。

## 5. 回退到厂商 PL

```sh
systemctl disable --now ai-sys-bitstream.service
systemctl disable --now plbram.service            # 可选
systemctl restart tcp-image-server.service        # 它会因找不到我们的 PL 而反复重启, 可 disable
# 然后 insmod /root/workspace/driver/fpgadrv.ko （或直接重启走 rc.local）
```

最彻底的回退就是**只重启**：`ai-sys-bitstream` 一 disable，PL 就一直是厂商的。

## 6. 相关脚本

| 文件 | 作用 |
| --- | --- |
| `boot/load_ai_sys.sh` | 开机烧比特流的脚本（装到 `/usr/local/sbin/`） |
| `boot/ai-sys-bitstream.service` | systemd oneshot（装到 `/etc/systemd/system/`） |
| `ai/net/_boot_install.py` | `check` / `install` / `now` / `reboot` 四个模式，PC 端一键操作 |
| `ai/net/load_ai_sys.py` | 手动加载（等价于跑一次那个脚本） |
