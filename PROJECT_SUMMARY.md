# 自动高光剪辑 — 项目总结

## 已实现功能

### 1. 网页端批量视频下载
- 启动 `python web_app.py` → `http://127.0.0.1:7860/`
- 粘贴 YouTube URL（一行一个），点击"开始下载"
- 自动从浏览器读取 Cookies（Chrome/Safari/Firefox），无需手动导出
- 支持分辨率选择、代理、字幕、请求间隔
- 下载结果存入 `downloads/` 目录（视频 + metadata.json）

### 2. Gemini 高光分析 + 自动剪辑
- 输入 YouTube URL → Gemini 分析视频时间线 → 返回高光片段（时间 + 评分 + 描述）
- 自动下载原片 → 按高光时间点剪辑 → 输出片段到 `output/`
- 支持 OpenAI 兼容 API（GRSAI）和 Gemini 原生 API 自动适配
- 当前模型：`gemini-3.5-flash`

### 3. 命令行独立下载工具
- `yt_downloader.py` 可独立使用，不依赖 Flask
- 支持分辨率、音频提取、字幕、播放列表、限速、代理、Cookies

### 4. 测试覆盖
| 测试 | 类型 | 说明 |
|------|------|------|
| 基础单元测试 (8项) | 自动运行 | URL解析、文件名安全、JSON解析、Netscape转换 |
| YouTube 下载测试 | 需 `RUN_YOUTUBE_DOWNLOAD_TEST=1` | 真实下载验证（浏览器 Cookies） |
| Gemini 分析测试 | 需 `RUN_GEMINI_ANALYZE_TEST=1` | 真实 API 调用验证高光分析 |
| Lint 检查 | `ruff check` | 全部通过 |

## 本次修改

1. 默认 Cookies 模式从 `file` 改为 **`browser`**，自动检测本地 Chrome/Safari
2. `playerClient` 默认从 `default` 改为 **`mweb`**
3. Gemini 模型从 `gemini-2.5-pro` 改为 **`gemini-3.5-flash`**
4. 适配 OpenAI 兼容 API：`gemini_analyzer.py` 自动识别 `generativelanguage.googleapis.com` vs 其他代理
5. 删除冗余文件：`src/py_uv_uvx_py_uv_add/`、`highlight_clipper/run.bat`、`优化1.md`、`AGENTS.md`、`SESSION_SUMMARY.md`
6. 重构 `pyproject.toml`：清理 scaffold 代码、修复 hatchling 构建
7. 新增 `tests/test_gemini_analyzer.py`：Gemini API 集成测试

## 需要完善

### a) YouTube 反爬对抗
- Cookies 过期策略：已切换到浏览器模式自动读取，但需要用户保持 Chrome 登录 YouTube
- 可以加入 PO Token 支持（yt-dlp 已支持 `--extractor-args youtube:po_token=...`）

### b) Gemini 视频分析增强
- 当前只通过 URL 传给 API，大视频需先下载再传文件
- 文件模式（base64）超过大小限制时需压缩，但 `_call_openai_compatible_api` 暂未实现 base64 传输（被删除）
- 需要支持本地视频文件分析

### c) 批量任务管理
- 无任务队列持久化，刷新页面后任务状态丢失
- 无法取消单个任务（只能全局停止）

### d) 前端体验
- 无进度条，只有文字日志
- 无历史记录/收藏夹
- Cookies 模式切换后需要手动刷新页面

### e) 项目结构
- `run_app.command` 仍用 pip+requirements.txt，建议改为 `uv run python web_app.py`
- `README.md` 安装说明还在用 pip

### f) 代码测试
- `highlight_clipper/pipeline.py` 暂无测试覆盖
- 需补本地视频文件的 Gemini 分析测试
