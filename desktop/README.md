# DARKSLIDE GR 桌面版（MyGo 外壳）

用 [MyGo](https://mygo.egoist.dev/) 把现有的 DARKSLIDE GR 包成 macOS 和 Windows 桌面应用。Python 程序一行不改：应用自带一份独立的 Python，启动时在 127.0.0.1 上运行 `launcher.py --no-browser`，再用系统 WebView（macOS 的 WKWebView、Windows 的 WebView2）打开页面。

- 用户不用自己装 Python，双击即可。
- 数据文件夹与脚本版相同（`~/Library/Application Support/Darkslide`、`%APPDATA%\Darkslide`），两种方式可以混用。
- 端口沿用 8787 起的前 20 个，页面来源保持不变，`localStorage` 里记住的设置照常有效。
- 页面里的「退出」会结束整个应用；关闭窗口或退出应用时，会先按同样方式请服务停止，3 秒内没停就强制结束。Windows 上服务进程绑在 Job Object 里，应用崩溃或被任务管理器结束时也会一起结束。
- 理光官网等外部链接用系统浏览器打开。下载的文件存到「下载」文件夹，并在 Finder 或资源管理器里显示。

## 外壳替页面补上的几处

页面代码不改；外壳用预加载脚本把下面几类请求交给 Go（只对本机服务的页面开放）：

- **耗时请求**（拟合、生成并核对等）：WebKit 的请求 60 秒没有回应就会放弃（Intel Mac 会遇到），改由 Go 发送，等到服务回应为止。
- **Windows 的下载**：实测中 WebView2 不下载 `blob:` 链接（导出 .cube、配方卡）；同一页面已经下载过文件后，再点「下载固件」也不会开始下载。所以在 Windows 上，带 `download` 的链接改由 Go 写进「下载」文件夹。设置 `DARKSLIDE_DOWNLOAD_BRIDGE=1` 可以在其他系统上测试这一路径。

## 构建

需要 Go 1.27 或更新版本，以及 Node.js 22.6 或更新版本（MyGo 用它读取 `mygo.config.ts`）。在 macOS 上打包 Windows 安装程序还需要 NSIS（`brew install makensis`）。

版本号取自 `darkslide/__init__.py` 的 `VERSION`，不用手动改。macOS 的 build 号（`CFBundleVersion`）取环境变量 `DARKSLIDE_BUILD`（CI 用运行编号），没有设置时用 commit 数。

```sh
cd desktop
python3 scripts/fetch_python.py          # 下载并核对 python-build-standalone（固定版本和 SHA-256）
LC_ALL=en_US.UTF-8 go tool mygo build -platform darwin/arm64,darwin/amd64,windows/amd64
```

产物在 `dist/<平台>/`：macOS 是 `.app` 和 `.dmg`，Windows 是 `Darkslide GR Setup <版本> amd64.exe`（按用户安装，不需要管理员权限）。Homebrew 的 makensis 在没有 UTF-8 locale 时会崩溃（`std::bad_alloc`），所以要加 `LC_ALL`。

开发时用 `go tool mygo dev`，程序文件直接取自仓库。用 `DARKSLIDE_PYTHON` 可以改用别的 Python。

## 授权

外壳（`desktop/` 下的程序）采用 MIT 许可。它是一个独立的程序，只通过本机 HTTP 和 DARKSLIDE GR 通信；DARKSLIDE GR 本体原样打包，仍是 GPL-2.0-only。外壳编译进去的 Go 模块（MyGo、purego、golang.org/x/…、Go 标准库）的许可全文，在 `DESKTOP_LICENSES.md` 里，这个文件也会放进 App。依赖有变化时，运行 `python3 desktop/scripts/licenses.py` 重新生成，CI 会检查它是不是最新的。

## 程序改动时要注意

外壳只依赖程序的三样东西：`launcher.py` 读 `PORT` 环境变量、接受 `--no-browser`，以及服务的 `POST /api/quit`。这三样不变，程序怎么改，重新打包就行，版本号也会自动跟上。另外两件事：

- 程序开始读仓库里新的顶层文件夹时，要把它加进 `mygo.config.ts` 的 `resources`。`scripts/check_resources.py` 会找出漏掉的，CI 每次都会跑。
- 新增耗时很长、以单个请求等待的接口时，要把它加进 `local.go` 的 `longRequests`，否则 Intel Mac 上可能碰到 WebKit 的 60 秒限制。

## 平台

| 平台 | Python | 拟合（Unicorn） |
|---|---|---|
| macOS Apple 芯片 | darwin-arm64 | 支持 |
| macOS Intel | darwin-amd64 | 支持 |
| Windows x64 | windows-amd64 | 支持 |

Windows 10 需要安装 WebView2 Runtime，Windows 11 已经自带。Unicorn 没有 Windows ARM64 版本，所以不提供这个平台。

## 自测构建

`-tags selftest` 会加入一个只监听 127.0.0.1 的控制通道（见 `selftest.go`）：可以在页面里执行 JavaScript、读取已完成的下载，并把本机文件交给页面，相当于拖入文件。正式构建不包含这个通道。

```sh
GOFLAGS=-tags=selftest go tool mygo build -platform darwin/arm64 -skip-dmg -o build-selftest
open -n --env DARKSLIDE_DATA=/tmp/ds --env DARKSLIDE_SELFTEST_PORT=9911 \
  --env DARKSLIDE_SELFTEST_DOWNLOADS=/tmp/dl "build-selftest/darwin-arm64/Darkslide GR.app"
```
