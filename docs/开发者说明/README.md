# DARKSLIDE GR 开发者说明

简体中文 | [English](../developer-notes/)

回到 [README](../../README.md)。

## 运行测试

运行测试：`python3 -m unittest discover -s tests`
部分原件测试使用 `DARKSLIDE_OFFICIAL_BIN`。完整测试集中的独立读回专项还要求 `DARKSLIDE_READBACK_ORIGINAL` 指向官方 GR III 2.10 解码载荷；缺少它会明确报错。IIIx 桌面专项可单独设置 `DARKSLIDE_DESKTOP_IIIX` 为官方 1.60 原件路径运行 `python3 -m unittest discover -s tests -p test_iiix_desktop.py`。这些专项不生成完整修改固件。

IIIx 完整候选专项：设置 `DARKSLIDE_IIIX_PRODUCT_BIN` 为官方 1.60 文件，运行 `python3 -m unittest discover -s tests -p test_iiix_generation.py`。此项会在临时数据目录生成完整修改候选并读回，结束后清理，不连接相机。运行期测试另需 `DARKSLIDE_GR3X_PAYLOAD` 和 Unicorn；完整格式器测试还需单独的本地研究夹具。

## 重建 ARM 代码片段

只有要重建随程序附带的 ARM 代码片段的开发者，才需要支持 armv7a-none-eabi 的 LLVM clang。这些片段由随附的、有许可的源代码编译而来，不是从官方固件里提取的。

## GR III 2.20 / GR IIIx 1.70 的地址换算

功能代码按 2.10 / 1.60 的地址编写；对 2.20 / 1.70 的工程，`darkslide/fwaddr.py` 按 `darkslide/data/reloc_220.json` 把地址换成新固件的（表外地址一律拒绝，2.10 / 1.60 不换算）。相关开发工具都只读、需要你自己的官方原件：

- `tools/build_reloc_220.py`：由地址对照表和四份解码原件生成换算表（只含地址和位移）。
- `tools/check_relocated_windows.py`：核对 2.20 / 1.70 的胶片改动与 2.10 / 1.60 只差地址。
- `tools/capture_v29_reference_220.py`：在前置检查全部通过后生成 2.20 胶片读回参考。
- `tools/build_film_arm_bundle_220.py`：按新地址重建 2.20 / 1.70 的 ARM 片段包（2.10 / 1.60 的包不动）。

## 给 AI 助手

替用户安装、配置「拟合」组件或放胶片图标的完整步骤，见 [AGENTS.md](../../AGENTS.md)。

## 桌面版外壳

桌面版的外壳源码和打包方式见 [`desktop/`](../../desktop/README.md)，采用 MIT 许可。

### 鲜艳第五位置

GR III 2.10 / GR IIIx 1.60 的胶片位置为 2、8、9、10、11；2 对应鲜艳。正片与负片不参与胶片替换。选中鲜艳时使用五位置布局，不选鲜艳时保留原四位置的字节输出。两种布局都保持解码后长度不变。

发布程序需保留 `darkslide/data/film_arm_vivid5_v1.json`、`darkslide/film_precompiled_vivid5_pin.py` 与 `darkslide/film_vivid_fifth_reference.json`；原四位置资源也必须保留。开发者可运行 `python3 tools/build_film_arm_bundle.py --vivid-five --check` 重编核对新资源，运行不带 `--vivid-five` 的 `--check` 核对原四位置片段。旧资源的源码元数据保留当时版本，重编检查仍逐项比较所有实际指令、地址和组合。终端用户无需编译器。

照片配方扩展暂不接受鲜艳位置；使用不扩容生成。五位置离线核对通过不等于机身测试完成，测试时应检查新鲜艳胶片、原四卷及保留的正片、负片，并覆盖切换、重启和 RAW 显影。
