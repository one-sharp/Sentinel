# 在另一台 Windows 电脑继续开发

仓库：[one-sharp/Sentinel](https://github.com/one-sharp/Sentinel)（私有）。当前迁移快照为 `v0.3.1-logic`，保留已有桌面、ASR、CAM++、VAD、DeepSeek、私人笔记及逻辑图。全局快捷键仍停用。

## 恢复源码和开发环境

新电脑需要 Windows x64、Python 3.12、Git 和 GitHub CLI。登录拥有此私有仓库访问权限的账户后：

```powershell
gh auth login
gh repo clone one-sharp/Sentinel
cd Sentinel
py -3.12 download_release_assets.py
Expand-Archive .downloads\Sentinel-dev-wheels-py312-win64.zip -DestinationPath .downloads
.\setup_dev.cmd
.\.venv\Scripts\python.exe restore_models.py --archive .downloads\Sentinel-models-v0.3.1.zip
.\.venv\Scripts\python.exe restore_models.py --verify
```

`setup_dev.cmd` 创建本机 `.venv`，从离线 wheel 包按 `requirements-lock.txt` 安装当前环境的版本；没有 `.env` 时才从 `.env.example` 创建，不覆盖已有配置。模型恢复程序按 `models-manifest.json` 校验五个模型文件，拒绝异常 ZIP 路径和不匹配的文件，也不会覆盖已有不同内容的模型。

模型和较大的安装附件采用8 MiB分块上传，避免弱网络连接中断后整包重传。`download_release_assets.py` 默认下载开发需要的模型和离线依赖，验证每块及完整文件 SHA256，自动还原原文件名；已有正确文件会复用。重复运行可继续下载缺失分块。分块清单保存在 `release/assets-manifest.json`，不是用户数据。

下载器沿用 Windows 的现有系统代理（环境变量优先），不修改全局网络配置。GitHub CLI 用于登录和读取发行元数据，文件通过 Python 标准 HTTPS 下载；令牌只在内存中使用，不传给重定向后的附件主机。下载中断只留下临时文件，校验成功后才发布为可复用分块。

需要安装版时运行 `py -3.12 download_release_assets.py --asset Sentinel-Setup.exe`，完成后双击 `.downloads\Sentinel-Setup.exe`。下载全部五个附件使用 `py -3.12 download_release_assets.py --all`。也可在 Release 页面手动下载指定文件的所有 `.partNNN`，放到 `.downloads`，再运行同一命令自动合并与校验。

依赖下载核验发现当前 `openai==3.46.0` 无法从所用包索引重新取得，因此发行版额外保存了当前已安装的31个发行包，重新封装为标准 wheel，保留上游元数据和许可文件，去除本机安装路径、缓存和自动生成的启动器。新环境由 pip 重建自己的启动器；没有替换 SDK 或改动运行后端。

随后双击 `start_sentinel.cmd`。打开默认待机，在「设置 → API 配置」填写自己的 Key、模型和地址；也可编辑本机 `.env`。新设备的 API Key 需要重新配置，原设备的 DPAPI 加密配置不能直接跨电脑解密。

图标、截图、源码、测试、PyInstaller spec、Inno 安装脚本和构建报告都在 Git 中。依赖清单包含原开发环境的运行及构建包，不把整个 `.venv` 跨设备复制。

## 发行附件

[下载页面](https://github.com/one-sharp/Sentinel/releases/tag/v0.3.1-logic)：

| 文件 | 用途 |
| --- | --- |
| `Sentinel-models-v0.3.1.zip` | 当前开发版使用的 Paraformer INT8、tokens、CAM++ 和 VAD，开发恢复只需要这个模型包 |
| `Sentinel-dev-wheels-py312-win64.zip` | 当前31个 Python 依赖的离线安装包，用于 Windows x64 / Python 3.12 |
| `Sentinel-Setup.exe` | 已实测的 Windows 安装版，无需另外安装 Python |
| `Sentinel-portable-v0.3.1.zip` | 完整 onedir 运行目录，解压后运行 `Sentinel\Sentinel.exe` |
| `model.tar.bz2` | 原官方 ASR 完整归档，含 FP32/INT8 权重和官方测试音频；当前运行无需额外下载这个大包 |
| `SHA256SUMS.txt` | 以上文件 SHA256 校验值 |

模型来源见 [MODEL_SOURCES.md](MODEL_SOURCES.md)。大文件使用 Releases：[GitHub 的普通仓库拒绝大于100 MiB的文件](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)，[单个发行附件须小于2 GiB](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -v
.\build_windows.cmd
```

编译安装包另外需要 Inno Setup 6；`build_windows.cmd` 会寻找 `.build_tools\inno\ISCC.exe` 或标准安装目录中的编译器。详见 [PACKAGING.md](PACKAGING.md)。发布附件可运行 `python packaging\prepare_github_release.py` 重新生成，前提是已构建 EXE 并保留原始 ASR 归档。

原有麦克风、联网测试均需显式运行，不属于普通单元测试：

```powershell
$env:SENTINEL_DATA_DIR=Join-Path $PWD 'packaging-qa\manual'
# 在此隔离目录配置好自己的 API Key，再按需要运行：
Start-Process .\dist\Sentinel\Sentinel.exe -ArgumentList '--self-test','--test-microphone','--test-llm' -Wait
```

## 本机私人数据

真实 `.env`、API Key、日志、私人笔记、声纹、图文档、桌面偏好和 QA 目录均不上传。原设备上的这些文件保持不变。

应用数据默认位于 `%LOCALAPPDATA%\Sentinel`。如果需要个人数据迁移，应另行通过自己的安全方式传输；API Key 请在新电脑重新输入。源项目中的旧日志也留在本机。

## 后续同步

```powershell
git pull
# 修改并测试后：
git add <修改的源码文件>
git commit -m "Describe the change"
git push
```

提交前确认没有私人数据。仓库忽略规则包含 `.env`、本地用户数据、模型、压缩包、构建目录和安装程序；模型及二进制更新后另发 Release。

## 此次换机恢复自检（2026-11-17）

已经从此 GitHub 仓库重新克隆源码，在新的 Python 3.12 虚拟环境安装31个依赖并恢复五个运行模型。克隆副本的89项单元测试中87项通过、2项可选原生测试跳过；恢复的 Paraformer 实际解码和 CAM++ 192维特征推理通过。原开发环境没有被替换。

加入分块下载及安全校验后，最终源码的97项测试中95项通过、2项可选原生测试跳过。运行模型已经从本仓库的真实 Release 下载全部30个分块，合并后完整 ZIP 的 SHA256 与原始模型包一致。未把本地新虚拟环境测试称为另一台实体电脑实测。
