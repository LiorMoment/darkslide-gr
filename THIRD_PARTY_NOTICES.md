# 第三方材料说明

本文件说明 DARKSLIDE GR 涉及哪些不属于作者的材料，以及它们的权利归属。作者自己编写的内容见 [AUTHOR_STATEMENT.md](AUTHOR_STATEMENT.md)。

## 1. 相机固件与厂商资源

- 相机固件，以及其中的图标、菜单文字、图像处理参数等资源，版权归相机厂商所有。
- 本仓库**不包含**任何固件文件，也不包含从固件中提取的数据表、图标原图或代码片段；代码里只有核对所需的少量地址、长度、哈希值和字节特征。
- 工具运行时读取你自备的固件，生成的文件仍含有厂商内容。这些内容的权利不会因为本工具的处理而改变：你可以自己使用，但未经权利人许可，**不应公开分发或销售**生成的完整固件。
- README 截图（`docs/images/`）中可见的原厂菜单图标缩略图和（已模糊处理的）原厂关机画面，仅用于展示工具界面，权利归厂商所有。
- `templates/` 中只记录"允许修改哪些位置、按什么规则编码、取值范围是多少"，以及各预设详细设置在相机菜单里显示的原厂默认值（只用于显示），不含厂商的原始内容。

## 2. 商标

- RICOH、GR 等是其各自权利人的商标。本项目使用这些名称只为说明兼容的机型和固件版本，不代表与权利人存在关联或获得认可。
- Kodak、Portra、Gold、UltraMax、Ektar、Ektacolor、Vision3 等是 Eastman Kodak Company 的商标；Fujifilm、Pro 400H、X-Tra、C200、Crystal Archive 等是富士胶片株式会社的商标；Adobe、Lightroom、Camera Raw 是 Adobe 的商标。这些名称只用来说明胶片风格的参考来源和可导入的文件类型。内置胶片是根据公开的胶片特性数据模拟出来的近似效果，不是这些厂商的产品或官方配置，本项目也与这些厂商没有关联。
- 你在预设名里填写的文字如包含他人商标，由你自己负责。
- 本仓库**不收录**品牌包装图、商标图形或从他人作品复制的图片。工具不内置示例图标，「原厂风格」文字图标由程序按你输入的字母当场生成；你导入的图片由你自己负责其权利。

## 3. 运行依赖

程序使用 Python 标准库和浏览器原生 JavaScript，基本功能不需要安装任何第三方库。「拟合」功能（把导入的 LUT 或调色写进相机）需要一个可选组件 Unicorn，用户同意后才下载，不随仓库分发；没装时其他功能照常。NumPy 只用于开发者的拟合工具，普通使用不需要。

| 组件 | 版本 | 来源 | 许可 |
|---|---|---|---|
| NumPy（仅开发者拟合工具） | 调用方环境版本 | https://numpy.org/ | BSD-3-Clause |
| Unicorn（「拟合」功能的可选组件） | 2.1.4 | https://github.com/unicorn-engine/unicorn/tree/2.1.4 | 原生引擎 GPL-2.0；Python wheel 元数据标记 BSD，不能将引擎视为 BSD |
| opencc-js（仅开发时，用来生成 `tools/i18n/zh-Hant.json` 与 `web/i18n-data.js` 里的繁体对照表；不随程序运行） | 1.0.5 | https://github.com/nk2028/opencc-js | 代码 MIT；所含 OpenCC 词典 Apache-2.0（https://github.com/BYVoid/OpenCC） |

### 桌面版（`desktop/`）

桌面版安装包里另有两部分第三方内容，许可全文随安装包附上：

| 组件 | 版本 | 来源 | 许可 |
|---|---|---|---|
| 桌面版外壳（`desktop/`，作者 [@Ady0402](https://github.com/Ady0402)） | 随本仓库 | 本仓库 [#6](https://github.com/LiorMoment/darkslide-gr/pull/6) | MIT；外壳编译进的 MyGo、purego、golang.org/x/…、Go 标准库许可全文见 `desktop/DESKTOP_LICENSES.md` |
| 自带的 Python（python-build-standalone） | CPython 3.13.16（20261013） | https://github.com/astral-sh/python-build-standalone | Python 许可（PSF-2.0）；其中编译进的 OpenSSL、bzip2、xz、libffi、mpdecimal、SQLite、zlib 等许可全文见 `desktop/python_licenses/` |

外壳和 DARKSLIDE GR 本体是两个独立的程序，只通过本机网络通信，各自适用自己的许可；本体仍是 GPL-2.0-only。

## 4. 胶片曲线数据（CC BY-SA 4.0）

`film_curves/` 文件夹里的胶片曲线数据来自 **spektrafilm**（作者 Andrea Volpato，https://github.com/andreavolpato/spektrafilm ），按 CC BY-SA 4.0 使用并经 LiorMoment 修改（导出后设定白点，再拟合为相机设置）。这部分数据单独适用 CC BY-SA 4.0，不适用本项目的 GPL-2.0-only；署名、许可全文与改动记录见该文件夹。DARKSLIDE GR 没有使用 spektrafilm 的程序代码。

## 5. 用户输入

你导入的固件、图片和文字，权利归各自的权利人。工具不会上传它们，所有处理都在你的电脑上完成。

## 6. 胶片功能采用的开源代码

- 项目：[idontknownihil/gr3-film-mods](https://github.com/idontknownihil/gr3-film-mods)，固定提交04d5d5dd6e85d80089c6200bd0f0de5fff2d658f，Copyright (c) 2026 idontknownihil，MIT License。
- 采用部分：film_cave.py 的曲线切换与后矩阵模板、后矩阵数据打包；armasm.py 的绝对分支预处理。完整 MIT 许可随附于 darkslide/_vendor/GR3_FILM_MODS_LICENSE.txt，逐文件来源和改动见 darkslide/_vendor/gr3_film_hooks_source.json。
- LiorMoment 的适配：限定交叉冲洗、HDR 色调、怀旧、银流冲洗四个预设，使用完整 256 点曲线指针；添加显式范围、原字、目标记录与计划校验。作者自己的适配代码按本项目 GPL-2.0-only 授权，上游 MIT 版权与许可保留。
- 四卷固定布局采用 film_cave.py 的33节点及step-8插值原理。从只读节点直接取样，不新增展开缓存；LiorMoment 按当前记录选择四卷，自行接入两级定点组合及原RGB/Y输出，保留原库起点回退保护，不临时替换共享曲线库指针。节点拟合使用本地精确有理数实现，存储近似会报告误差。固定来源、采用范围与修改见 darkslide/_vendor/gr3_four_curve_source.json；MIT版权及许可同上。
- 没有采用上游整套 CLI、其解包/打包子模块、RAW 降噪、图标或预设名代码；这些功能继续使用作者自己的实现。
- GR III 2.10 与 GR IIIx 1.60 的不扩容胶片入口随程序分发预编译 ARM 指令片段。片段由本仓库的自有代码和上述 MIT 衍生模板编译产生，不是从厂商固件提取；本地修改按 GPL-2.0-only 提供对应源码、构建工具和改动说明，保留上游 MIT 版权及许可。开发者重建片段需要 LLVM clang，终端用户运行这两个入口不需要编译器。
- 可选槽位写入器还采用 make_slots.py 的原厂指针恢复与交叉冲洗回退逻辑、make_films.py 的 HDR/怀旧效果去除位置；make_slots16.py 的零表采用情况见下文「不扩容准备模块」。本地改为四个原生位置任意选择，私有记录、矩阵与完整曲线，缺数据即拒绝。逐文件 SHA 和本地改动见 darkslide/_vendor/selective_film_source.json，仍适用上述 MIT 许可。不采用新增枚举、色相分区或 GR IV 数据。
- 相机数据接入采用 make_film.py 的 Q13 量化算法，以及 cube_fit.py 的完整256节点加权最小二乘曲线拟合算法。后者裁切为开发函数，由调用方提供样本，懒加载 NumPy；未执行上游 CLI、未采用其基础伽马文件。来源、固定提交、SHA及修改见 darkslide/_vendor/gr3_fit_numeric_source.json，MIT 许可同上。
- 8卷现成 spektrafilm 衍生拟合数据（作者 Andrea Volpato，拟合修改者 idontknownihil，CC BY-SA 4.0）原文保留在 film_curves/upstream_gr3_fits/；2026-10-15 起 16 卷都改由 DARKSLIDE GR 从设定白点后的导出重新拟合，这 8 卷不再使用，清单中标为 in_use false 并写明原因。数据许可与代码 MIT/GPL-2.0-only 分开。

- 用户 LUT／调色拟合（darkslide/_vendor/gr3_lut_fit.py）移植同一固定提交的 cube_fit.py（相机模型、M_from、矩阵与曲线交替拟合、fit_curve）、cca1_fit.py（白保持的曲线后矩阵）和 lab_fit.py（按 CIEDE2000 的感知精调），由 LiorMoment 改为只用 Python 标准库：曲线求解改带状 Cholesky，六个矩阵项改小型 Levenberg-Marquardt；只接受 sRGB 编码的 .cube 目标；样本改为自己生成的灰阶、色相环、肤色与随机色，不用 ColorChecker 数据表；基础伽马由调用方在运行时从用户原件读出，不使用上游基础伽马文件。来源与修改见 darkslide/_vendor/gr3_lut_fit_source.json，MIT 许可同上。
- 只读基础曲线适配采用同一固定提交的 emu.py、run_tuning.py 初始化与服务桩流程，裁切为调用方原件输入、限定 ROM 只读、有界执行的隔离子进程。来源和修改见 darkslide/_vendor/gr3_base_emulator_source.json，MIT 许可同上。不采用其基础曲线数组或固件解包子模块。

- 交叉冲洗最小验证模块复用上述 MIT 后矩阵模板、数据打包和编译适配；新的固定曲线位置选择及代码页内记录布局由 LiorMoment 实现。该模块不采用上游运行时共享指针切换，不引入新的第三方数据。
- Unicorn 是「拟合」功能的可选组件；原生引擎许可全文见 https://github.com/unicorn-engine/unicorn/blob/2.1.4/COPYING 。它含 GPLv2 引擎及单独说明的组件权利，不能只根据 wheel 的 BSD 标签判断整个依赖。DARKSLIDE GR 不分发其二进制：「拟合」组件由 tools/setup_fitting.py 或页面在用户同意后，从 PyPI 下载 Unicorn 2.1.4 官方 wheel（固定版本，核对 PyPI 公布的 SHA-256），解压到用户的 DARKSLIDE GR 数据文件夹（darkslide/fitting_setup.py）。另有纯 Python 读取器（darkslide/base_curve_python.py）作为备用，结果与 Unicorn 逐值核对一致，本版不启用。以后如改为随包分发，需随附依赖的许可与相应源码提供方式。

### 不扩容准备模块

`darkslide/film_inplace_cache.py` 延续采用上述固定提交 `make_slots16.py` 的零表身份核对与八个指针重定位部分（MIT，Copyright (c) 2026 idontknownihil）；由 LiorMoment 修改为四位置部分安装、严格身份检查及稀疏窗口计划。来源、原文件哈希和修改范围见 `darkslide/_vendor/gr3_zero_table_source.json`，许可全文仍见 `GR3_FILM_MODS_LICENSE.txt`。重复矩阵的无损重建为本项目实现。未采用 GR IV 数据或上游整包构建器。

- GR IIIx 1.60 的只读稀疏核对复用同一 33 节点与零表方案，绑定 IIIx 官方输入与独立读回；来源记录见 `darkslide/gr3x_inplace_sources.json`，上述 MIT 许可及数据 CC BY-SA 许可继续保留。照片配方扩展 1.0 不提供。
