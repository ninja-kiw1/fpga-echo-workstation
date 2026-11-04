# 实际安装与核查清单（2026-09-07）

## 已核实的主机状态

- Windows build：`10.0.26200`（64 位）
- GPU：NVIDIA GeForce RTX 4060 Laptop GPU，8188 MiB，驱动 610.47
- Anaconda：`C:\Users\21888\anaconda3`，Conda 25.5.1
- 原有环境：`base`、`yolo_env`；本次未修改它们
- 新环境：`srtp-vision`
- 系统命令解析顺序：裸 `python` 首先命中 `C:\msys64\mingw64\bin\python.exe`（3.11.10）；裸 `pip` 命中 Anaconda base 的 `Scripts\pip.exe`。因此日常必须先激活环境，并使用 `python -m pip`。
- VS Code：1.135.0 x64
- Python 扩展：`ms-python.python@2026.4.0`
- Jupyter 扩展：`ms-toolsai.jupyter@2025.9.1`

## srtp-vision 直接依赖实际版本

| 组件 | 版本 |
|---|---:|
| Python | 3.11.16 |
| NumPy | 2.2.6 |
| SciPy | 1.15.3 |
| opencv-contrib-python | 4.11.0.86（`cv2` 4.11.0） |
| Matplotlib | 3.10.3 |
| pandas | 2.3.0 |
| scikit-image | 0.25.2 |
| JupyterLab | 4.4.3 |
| ipykernel | 6.29.5 |

`pip check` 返回 `No broken requirements found`。OpenCV 包扫描只发现 `opencv-contrib-python==4.11.0.86`，没有 `opencv-python`、`opencv-python-headless` 或 `opencv-contrib-python-headless`。

完整传递依赖见 `requirements-lock.txt`；Conda 基础层见 `conda-explicit-win-64.txt`。

## 本次创建或修改的范围

- 新建 Conda 环境目录 `C:\Users\21888\anaconda3\envs\srtp-vision`
- 注册用户 Jupyter kernelspec：`C:\Users\21888\AppData\Roaming\jupyter\kernels\srtp-vision`
- 新建 `E:\SRTP\.vscode` 工作区配置以及 `environment`、`scripts`、`verification_results` 目录
- 没有修改全局 PATH，没有升级 base，没有修改 `yolo_env`，没有卸载现有软件
