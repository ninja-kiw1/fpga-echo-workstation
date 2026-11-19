# 板端界面缩放（GTK 兼容层改动）2026-11-19

配合「把板子 X 输出分辨率降到 1280×720」用的。板端界面是按 1920×1080 设计的
（主窗口自然尺寸 1420×996），直接降到 1280×720（工作区 1280×694）右栏会被切掉，
所以给 tkinter→GTK3 兼容层加了一个**全局缩放系数** `WS_UI_SCALE`。

这里放的是**改过的三个文件**，装到板上 `/data/ws2026/shim/tkinter/`：

| 文件 | md5 | 改了什么 |
| --- | --- | --- |
| `_gtkbase.py` | `4b2ce465e0e437ae4c30bce1774fc023` | 新增 `UI_SCALE` / `sx()`；`font_spec()` 统一按系数缩字号 |
| `_canvas.py` | `97b7278ae1e358060e2b20e27a4b61af` | 画布按系数缩控件尺寸、图元坐标、线宽、贴图；鼠标坐标除回来 |
| `_widgets.py` | `139487d376f942e523a0253367a8b164` | Frame 的 width/height、Label 图片尺寸、Scale 的 length 按系数缩 |

没改的（板上保持原样）：`__init__.py`、`_widgets.py` 以外的 ttk/消息框等、
`bootstrap.py`、`np_compat.py`、`pil_compat.py`、`pil_imagetk.py`、`scipy_compat.py`。

## 怎么生效

```sh
# 1) 覆盖三个文件（先备份）
cp -a /data/ws2026/shim/tkinter/{_gtkbase,_canvas,_widgets}.py /data/ws2026/_bak_scale20261119/
cp  _gtkbase.py _canvas.py _widgets.py /data/ws2026/shim/tkinter/
rm -rf /data/ws2026/shim/tkinter/__pycache__

# 2) 启动器里带上系数（launch.sh，已加在第 38 行）
export WS_UI_SCALE=${WS_UI_SCALE:-0.72}
```

`WS_UI_SCALE` 不设就是 `1.0`，行为与改动前逐字节一致。
**必须经 `launch.sh` 启动**：直接 `python3 ws_main.py` 拿不到这个变量，界面会是没缩放的旧样子。

## 设计取舍

只缩两样东西：**字号**（所有控件与画布文字都走 `font_spec()`，一处就够）和
**画布**（`_canvas` 里按同一系数缩坐标/线宽/贴图）。固定边距 `padx/pady` 和空白框
不缩 —— 留白比原来略宽一点点，但绝不会把控件挤坏，也不会让长中文串被截断。

画布的缩放是"设计坐标不动、绘制时乘系数"，所以：

- 上位机代码一行没改，图元坐标、点击坐标、`448 视图` 之类的口径全部保持原样；
- 鼠标事件在 `_canvas._dispatch` 里除以系数换回设计坐标；
- 整幅贴图（视频帧）缩放结果挂在 `PhotoImage` 对象上缓存，同一帧只缩一次。

## 回退

```sh
cp -a /data/ws2026/_bak_scale20261119/*.py /data/ws2026/shim/tkinter/
sed -i '/WS_UI_SCALE/d' /data/ws2026/board/launch.sh   # 或 cp launch.sh.bak_scale20261119 launch.sh
cp -a /etc/X11/xorg.conf.bak_20261119 /etc/X11/xorg.conf && systemctl restart lxdm
```

（分辨率那半在 `/etc/X11/xorg.conf` 的 `Modes`，备份在同目录 `xorg.conf.bak_20261119`。）
