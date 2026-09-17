# 通用定时图像点击器

一个面向 Windows 的开源定时图像识别点击工具。用户可以创建多套方案，为每套方案配置 1～5 个自定义步骤，在指定系统时间到达后依次识别并点击屏幕上的按钮。

当前版本：`2.0.0`

## 特性

- 多方案独立保存；
- 每个方案支持 1～5 个自定义步骤；
- 每一步可配置名称、识别超时和点击后等待；
- 框选屏幕上的真实按钮作为模板；
- 每一步都可以独立测试，测试不会点击；
- 安全演练模式只识别第一步；
- 正式模式在启用前显示完整点击链并二次确认；
- 支持隐私安全的方案导入、导出；
- 方案导出不包含按钮截图、屏幕坐标和日志；
- 支持多显示器和 Windows 显示缩放；
- `Esc` 全局紧急停止；
- 电脑休眠或严重延迟时自动取消，降低延迟误点风险；
- GitHub Actions 在 Windows 云端运行测试并构建单文件 `.exe`。

## 下载与使用

普通用户可以从项目的 Actions 构建产物或 Release 下载 `通用定时图像点击器.exe`，不需要单独安装 Python。

首次使用请阅读 [`docs/通用版使用说明.md`](docs/通用版使用说明.md)。

## 从源码运行

要求：

- Windows 10 或 Windows 11；
- Python 3.9 或更高版本。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\universal_clicker.py
```

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建 Windows 单文件程序

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

构建结果位于：

`dist\通用定时图像点击器.exe`

## GitHub 云端测试与构建

仓库包含两个工作流：

- `ci.yml`：每次推送和 Pull Request 都在 `windows-latest` 上使用 Python 3.9 和 3.12 运行测试；
- `build-windows.yml`：手动触发或推送 `v*` 标签时，在 Windows 云端测试并构建 `.exe`，随后上传为 Actions Artifact；使用 `v*` 标签触发时还会自动创建 GitHub Release。

上传到 GitHub 后，进入项目的 **Actions** 页面即可查看真实云端运行记录。

完整上传步骤参阅 [`docs/上传到GitHub说明.md`](docs/上传到GitHub说明.md)。

## 本地数据与隐私

运行数据保存在：

`%LOCALAPPDATA%\通用定时图像点击器\`

该目录包含本机方案、按钮截图、屏幕坐标和运行记录，不属于源码仓库，也不应提交到 GitHub。

导出的分享方案仅包含流程设置。接收者必须在自己的电脑上重新框选按钮。

## 技术原理

程序使用：

- `mss` 截取限定屏幕区域；
- OpenCV 灰度模板匹配定位目标；
- Windows 系统时钟进行跨秒触发；
- Windows 鼠标事件执行点击；
- Tkinter 提供本地图形界面；
- PyInstaller 生成单文件程序。

为了降低临界时刻开销，程序会在目标时间前预读取模板并初始化截屏。Windows、网页渲染、网络和服务器均不是硬实时环境，因此无法保证严格在某一个毫秒内完成点击。

## 安全边界

本工具不会也不应：

- 绕过验证码；
- 绕过登录验证；
- 绕过平台风控、访问控制或付费限制；
- 在用户无权操作的账号或页面上使用；
- 保证预约、抢购或订单一定成功。

请在使用前确认目标平台允许相关自动化操作。正式模式可能产生真实预约、订单或费用，使用者必须自行核对结果并承担操作责任。

## 参与贡献

请参阅 [`CONTRIBUTING.md`](CONTRIBUTING.md)。安全问题请参阅 [`SECURITY.md`](SECURITY.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
