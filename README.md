# Gemini Live Translate (Windows)

实时翻译浮动字幕条，基于 Google Gemini Live API。捕获系统声音或麦克风，实时转写并翻译成目标语言，以浮动 HUD 形式叠加在屏幕上。

## 功能

- **音源**：系统音频（WASAPI loopback）或麦克风
- **实时翻译**：调用 Gemini Live API，边说边翻
- **浮动 HUD**：透明置顶字幕条，鼠标拖动 / 四角缩放 / hover 显示控制栏，并会在多屏变化后自动回到可见区域
- **目标语言**：17 种语言可选（中文、英文、西班牙语、日语等）
- **TTS 回放**：可选把翻译结果用目标语言念出来
- **API 代理**：支持自定义 API Base URL（用于走代理或自建中转）
- **配置持久化**：API key 等设置保存在 `%APPDATA%\gemini-live-translate\`

## 系统要求

- Windows 10 / 11（64 位）
- 如从源码运行：Python 3.11+
- 如运行打包版：无需 Python，下载release里的.exe直接运行即可

## 从源码运行

### 1. 准备 Python 环境

安装 [Python 3.11+](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**。

### 2. 一键启动

双击 `run.bat`。脚本会自动：

1. 创建虚拟环境 `.venv`（首次）
2. 安装依赖 `pip install -r requirements.txt`
3. 启动应用 `python main.py`

如果想手动操作：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 打包成 exe

双击 `build.bat`。脚本会自动：

1. 创建 / 复用 `.venv`
2. 安装运行时依赖 + PyInstaller
3. 调用 PyInstaller 打包为单 exe 文件
4. 输出 `dist\gemini-live-translate.exe`（默认禁用 UPX，减少部分杀软误报和原生依赖兼容风险）

打包完成后，把 `dist\gemini-live-translate.exe` 拷贝到任何 Windows 机器双击运行即可，不需要安装 Python。

> 首次启动 onefile 模式的 exe 会稍慢（1-3 秒，解压到 temp），后续启动正常。

## 首次使用

1. 启动应用后，任务栏右下角出现托盘图标
2. **右键托盘 → Settings...**
3. 填入 Gemini API Key（[在 Google AI Studio 申请](https://aistudio.google.com/apikey)）
4. 选择目标语言（默认中文）
5. 选择音源（默认系统音频）
6. Save
7. **右键托盘 → Start / Stop**

## 配置文件位置

```
%APPDATA%\gemini-live-translate\settings.json
```

即 `C:\Users\<你的用户名>\AppData\Roaming\gemini-live-translate\settings.json`。

**注意**：API key 以明文存储，请勿在不信任的机器上使用。同机的其他进程理论上可读取。

## 项目结构

```
gemini-live-translate/
├── main.py              # 入口：QApplication + 托盘 + 信号编排
├── audio.py             # WASAPI loopback / 麦克风采集 + 24kHz 播放
├── gemini_client.py     # Gemini Live WebSocket 客户端 (QThread + asyncio)
├── hud_window.py        # 浮动 HUD：透明置顶 + 拖动 + 四角缩放 + 字幕
├── settings.py          # AppSettings dataclass + JSON 持久化
├── settings_window.py   # 设置对话框 UI
├── pcm_processor.py     # PCM16 下采样 + 分块
├── requirements.txt     # 运行时依赖
├── build.bat            # 一键打包脚本 (PyInstaller)
├── run.bat              # 一键源码启动脚本
└── .gitignore
```

## 依赖

| 包 | 用途 |
|----|------|
| PySide6 | GUI 框架（QApplication / QWidget / 系统托盘） |
| PyAudioWPatch | 音频采集与播放（带 WASAPI loopback 支持） |
| numpy | PCM 数据处理 |
| scipy | 音频重采样 (`scipy.signal.resample_poly`) |
| websockets | Gemini Live WebSocket 客户端 |

## 已知行为

- **WASAPI loopback 静音时不发数据**：选 "system audio" 时如果系统没在播放任何声音，HUD 字幕可能不动。这是 Windows API 的正常行为，开始播放音频后会自动恢复。
- **首次启动 API key 为空**：会话无法启动，需先在 Settings 填入。
- **修改 API key / 语言 / 音源 / echo 后**：会自动重启会话以应用新配置。
- **Windows Defender 误报**：PyInstaller onefile 模式偶发被误报；当前构建默认禁用 UPX，如仍误报可加白名单。

## 故障排查

**点 Settings 没反应**：检查 API key 是否已填，看 stderr 输出是否有异常。

**托盘图标不见**：Windows 11 默认折叠托盘，点任务栏右下角 `^` 展开。

## License

仅供学习与个人使用。Gemini API 的使用受 Google 服务条款约束。本项目中所有 Gemini 商标归属 Google。

## 友链

感谢 [LinuxDo](https://linux.do) 社区佬友们的交流与分享，以及各公益站提供的免费apikey让我一个小白也能写点东西
