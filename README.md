# 自动高光剪辑

一个面向 YouTube 视频的本地桌面工具，支持两种工作流：

1. 批量输入 YouTube URL，下载原视频到指定位置。
2. 批量输入 YouTube URL，调用 Gemini 分析高光时间段，下载原视频，并把原视频、高光片段、分析结果保存到同一个视频文件夹。

## 运行环境

- Python 3.10+（项目启动脚本会优先使用 Codex 自带 Python 3.12）
- FFmpeg / FFprobe（剪辑和读取视频信息需要）
- Gemini API Key

安装 Python 依赖：

```bash
pip install -r requirements.txt
```

启动界面：

```bash
python web_app.py
```

也可以运行：

```bash
python app.py
```

macOS 可以双击 `run_app.command`，它会启动本地服务并打开浏览器。

## 配置

本地配置文件使用 `env`，不会提交到 Git。

常用字段：

```env
GEMINI_API_KEY=你的Key
GEMINI_API_BASE=https://generativelanguage.googleapis.com
GEMINI_MODEL=gemini-3.5-flash
```

如果你使用兼容代理，也可以继续使用已有字段：

```env
GRSAI_OPENAI_API_KEY=你的Key
GRSAI_OPENAI_API_BASE=https://grsai.dakka.com.cn
NANO_BANANA2_MODEL=gemini-3.5-flash
```

## 输出结构

下载视频：

```text
downloads/
  视频标题/
    original.mp4
    metadata.json
```

分析剪辑：

```text
output/
  视频标题/
    original.mp4
    manifest.json
    highlights/
      01_高光描述_评分95_20s.mp4
      02_高光描述_评分90_18s.mp4
```

## YouTube 下载失败时

这个项目直接使用 `yt-dlp` 最新 master 代码。YouTube 现在会逐步要求 PO Token，部分视频还会触发登录、年龄、地区或频率限制。

优先尝试这些设置：

1. `YouTube Client` 先用 `default`，失败再试 `mweb` 或 `tv_downgraded,web_safari`。
2. 如果页面提示登录、年龄限制或机器人验证，选择浏览器 cookies，或填写导出的 cookies 文件。
3. 批量下载时保留 5-10 秒请求间隔，降低触发频率限制的概率。

## 说明

- URL 一行一个。
- 双击结果列表中的文件夹路径可以打开对应输出目录。
- 需要登录或地区限制的视频，可以选择 cookies 文件；下载原片时会传给 yt-dlp。
- 分析剪辑页会先把 YouTube URL 交给 Gemini 分析高光时间线，再下载原片并剪辑。
- 如果 Gemini 不能直接读取某个 YouTube URL，可以先用“下载视频”保存原片，再后续扩展为本地文件分析流程。

## 自检

不访问网络的基础测试：

```bash
.venv/bin/python -m unittest discover -s tests
```
