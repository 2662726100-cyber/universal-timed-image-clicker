# 上传到 GitHub 说明

## 一、创建空仓库

1. 登录 GitHub；
2. 点击右上角 `+`，选择 **New repository**；
3. 填写仓库名称，例如 `universal-timed-image-clicker`；
4. 选择 **Public**；
5. 不要勾选自动创建 README、`.gitignore` 或 License，因为源码包中已经包含；
6. 点击 **Create repository**。

## 二、从本地推送

解压开源项目包，在该目录打开 PowerShell，然后执行：

```powershell
git init
git add .
git commit -m "Initial open-source release v2.0.0"
git branch -M main
git remote add origin 你的GitHub仓库地址
git push -u origin main
```

“你的GitHub仓库地址”应替换成新仓库页面显示的 HTTPS 或 SSH 地址，不要原样复制这几个汉字。

## 三、检查云端测试

1. 打开 GitHub 仓库；
2. 进入 **Actions**；
3. 查看 **Windows tests**；
4. 确认 Python 3.9 和 3.12 两个任务均为绿色通过；
5. 如果失败，点开红色任务查看具体日志，不要在测试失败时发布 Release。

## 四、手动云端构建

1. 进入 **Actions**；
2. 打开 **Build Windows executable**；
3. 点击 **Run workflow**；
4. 构建完成后，在该次运行页面底部下载 `通用定时图像点击器-Windows` Artifact。

## 五、发布 2.0.0

测试通过后，在本地执行：

```powershell
git tag v2.0.0
git push origin v2.0.0
```

`v2.0.0` 标签会触发 Windows 云端测试和构建。构建成功后，工作流会自动创建 GitHub Release，并上传：

- `通用定时图像点击器.exe`；
- `通用版使用说明.md`。

## 六、建议的仓库设置

- 在 **Settings → Actions → General** 中保留 GitHub Actions 运行权限；
- 在 **Settings → Code security and analysis** 中启用 Dependabot alerts；
- 在 **Settings → Code security and analysis** 中启用 Private vulnerability reporting；
- 为 `main` 分支设置保护规则，要求 `Windows tests` 通过后才能合并 Pull Request；
- 不要上传 `%LOCALAPPDATA%\通用定时图像点击器\` 中的配置、按钮截图和日志。
