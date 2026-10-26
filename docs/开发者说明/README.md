# DARKSLIDE GR 开发者说明

简体中文 | [English](../developer-notes/)

回到 [README](../../README.md)。

## 运行测试

运行测试：`python3 -m unittest discover -s tests`
部分原件测试使用 `DARKSLIDE_OFFICIAL_BIN`。完整测试集中的独立读回专项还要求 `DARKSLIDE_READBACK_ORIGINAL` 指向官方 GR III 2.10 解码载荷；缺少它会明确报错。IIIx 桌面专项可单独设置 `DARKSLIDE_DESKTOP_IIIX` 为官方 1.60 原件路径运行 `python3 -m unittest discover -s tests -p test_iiix_desktop.py`。这些专项不生成完整修改固件。

IIIx 完整候选专项：设置 `DARKSLIDE_IIIX_PRODUCT_BIN` 为官方 1.60 文件，运行 `python3 -m unittest discover -s tests -p test_iiix_generation.py`。此项会在临时数据目录生成完整修改候选并读回，结束后清理，不连接相机。运行期测试另需 `DARKSLIDE_GR3X_PAYLOAD` 和 Unicorn；完整格式器测试还需单独的本地研究夹具。

## 重建 ARM 代码片段

只有要重建随程序附带的 ARM 代码片段的开发者，才需要支持 armv7a-none-eabi 的 LLVM clang。这些片段由随附的、有许可的源代码编译而来，不是从官方固件里提取的。

## 给 AI 助手

替用户安装、配置「拟合」组件或放胶片图标的完整步骤，见 [AGENTS.md](../../AGENTS.md)。

## 桌面版外壳

桌面版的外壳源码和打包方式见 [`desktop/`](../../desktop/README.md)，采用 MIT 许可。
