import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
import yt_dlp

FRAME_INTERVAL_SECONDS = 2
MAX_ANALYSIS_FRAMES = 80

TIMELINE_PROMPT = """你是一名专业短视频内容分析师。

你会收到一组按时间顺序排列的视频截图，每张截图都在文字中标注了对应时间戳。

请先完整理解视频内容，再输出结构化时间轴。

要求：

1. 按视频真实顺序描述内容，不要跳过关键变化。
2. 每个时间段描述该段发生了什么、画面重点、动作变化、情绪变化、是否有剪辑价值。
3. 时间段不要机械平均，应按内容变化切分。
4. 重点识别：游戏开始、下注/抽奖/旋转、特殊模式、连续中奖、爆分、结算、主播反应、玩法规则、稀有图标、大奖数字。
5. 如果某段只是等待、重复动画、普通讲解、无变化操作，请明确标记为 low。
6. value 只能是 low / medium / high / peak。
7. 输出只允许是 JSON，不要 Markdown。

输出格式：
{
  "timeline": [
    {
      "start": 0,
      "end": 4,
      "content": "展示游戏界面并进入抽奖前准备",
      "event": "铺垫",
      "value": "medium",
      "reason": "提供玩法上下文"
    }
  ]
}
"""

CLIP_SELECTION_PROMPT = """你是一名专业短视频剪辑师。

下面是同一段视频的时间轴描述。请不要重新猜视频内容，只能根据 timeline 选择最适合剪辑的高光片段。

剪辑目标：
- 只保留观众愿意继续看的关键爽点、转折点、结果点。
- 优先保留 high / peak 片段。
- medium 只有在帮助理解 high / peak 时才作为前置上下文。
- low 一般不要进入剪辑。

剪辑规则：
1. 每个高光片段向前补 2-5 秒必要上下文。
2. 高光结束后最多保留 2-4 秒结果确认或主播反应。
3. 不要把等待、重复动画、普通讲解、无效尾巴剪进去。
4. 片段通常 8-25 秒，强爆点可到 40 秒。
5. 相邻高光如果间隔不超过 5 秒且中间不是明显无聊内容，可以合并。
6. 如果整个视频只有一个爆点，只返回一个片段。
7. 片段按时间顺序输出。
8. desc 控制在 10 字以内。

输出只允许是 JSON 数组：
[
  {"start": 3.0, "end": 15.0, "score": 95, "desc": "连续中奖"}
]

视频时间轴：
__TIMELINE_JSON__
"""

HIGHLIGHT_PROMPT ='''你是一名专业的短视频游戏/抽奖类内容剪辑师。

任务：
分析整个视频，找出真正适合二创剪辑、复盘展示、短视频留存的高光片段，并返回时间轴。

核心目标：

不是把视频浓缩成流水账。

而是只保留“观众愿意继续看下去”的关键爽点、转折点、结果点。

宁可少剪，也不要把普通过程、重复动画、平淡讲解都算作高光。

在合理情况下，返回片段总时长尽量控制在原视频时长的30%-40%左右。

这不是硬性规则：

- 如果视频本身很短，例如15秒视频里有10秒都在发生关键内容，可以返回约10秒
- 如果视频全程高能，可以适当超过40%
- 如果视频大部分平淡，只返回最有价值的10%-25%
- 不要为了凑比例而保留低价值片段

重点保留：

- 第一次进入游戏/开始抽奖的关键动作
- 抽奖开始前的期待感
- 特殊模式触发
- 连续中奖过程
- 奖励不断升级过程
- 巨奖爆发过程
- 主播激动反应
- 奖励结算展示
- 主播达人讲玩法时的核心卖点、关键规则、特别机制
- 展示游戏机、手机画面、玩法入口、下注/抽奖方式的关键瞬间
- 能让观众理解“怎么玩、为什么刺激、结果有多大”的必要上下文

避免：

- 长时间等待
- 无奖励旋转
- 重复动画
- 无解说空镜头
- 重复性质的小奖
- 普通寒暄、自我介绍、求关注
- 过长的玩法铺垫
- 没有新信息的界面展示
- 没有转折或结果的普通操作
- 仅仅因为画面在动就判定为高光

--------------------------------

高光判定阈值：

只有满足以下至少一类，才建议返回：

1. 玩法价值：解释了核心玩法、奖励机制、特殊规则、游戏亮点
2. 情绪价值：主播明显兴奋、惊讶、紧张、崩溃、反转
3. 结果价值：中奖、爆分、奖励升级、进入特殊模式、大奖结算
4. 叙事价值：从介绍进入实操、从普通过程进入高能过程、从铺垫进入结果
5. 视觉价值：画面出现明显稀有图标、大奖数字、奖励结算、关键游戏界面

如果一个片段只是“还在玩”“还在讲”“还在转”，但没有新信息、新情绪、新结果，应跳过。

评分建议：

- 90-100：强爆点，必须保留
- 80-89：明确高光，优先保留
- 70-79：有上下文价值，但只有在帮助理解强高光时才保留
- 70以下：一般不要返回

--------------------------------

片段数量和总量规则：

根据视频总时长动态决定：

0-3分钟：
通常返回1-4个片段

3-5分钟：
通常返回2-5个片段

5-10分钟：
通常返回3-7个片段

10分钟以上：
通常返回5-12个片段

这些只是参考，不要机械凑数量。

优先控制质量，其次考虑覆盖流程。

如果中前段只有介绍和普通操作，可以不保留或少保留。

如果后段才出现真正爆点，可以集中在后段。

--------------------------------

游戏机/老虎机/抽奖游戏特别规则：

如果出现以下事件，优先保留：

A级事件（必保留）

- FREE SPIN触发
- BONUS触发
- JACKPOT触发
- 特殊模式进入
- 巨额倍数出现
- 爆分开始
- 爆分结算
- 主播明显激动

B级事件（优先保留）

- 连续中奖
- 奖励升级
- 倍数快速增长
- 稀有图标出现
- 接近大奖时刻
- 主播讲清楚关键玩法后立刻实操
- 下注、抽奖、转盘、开箱、开奖前后的紧张节点

C级事件（可忽略）

- 普通中奖
- 小额奖励
- 无变化旋转
- 重复点击
- 重复展示同一界面
- 没有反应的普通讲解

--------------------------------

关于主播介绍类视频：

如果视频开头是：

- 主播介绍游戏
- 展示手机
- 讲解规则

当第一次点击开始抽奖时：

视为游戏正式开始。

一般从这里开始重点寻找高光。

介绍内容只保留以下情况：

- 讲到了核心玩法、奖励机制、特殊模式
- 展示了游戏机/游戏界面/玩法入口的关键画面
- 这段介绍是后续爆点必需的上下文
- 主播出现明显情绪反应或强烈推荐理由

普通自我介绍、泛泛而谈、重复说明，不保留。

--------------------------------

时间轴规则：

每个片段长度：

最短10秒
最长40秒

短视频例外：

如果原视频只有10-25秒，并且大部分都是真正高光，可以返回8-15秒左右的片段。

原则：

向前补足上下文：

例如BONUS在120秒触发，

可以返回：

110-140

而不是：

120-122

确保观众能看懂发生了什么。

但不要过度向前补：

如果爆点前是无效等待，只补3-8秒即可。

片段结束点必须非常克制：

- end 应停在“爆点结果清楚 + 主播关键反应/结算展示刚结束”的位置
- 高光结束后最多只保留2-5秒反应或结果确认
- 如果后面只是重复结算、无效停顿、普通聊天、继续无变化旋转，必须立即截断
- 不要把“爆点之后的平淡尾巴”剪进来
- 一个片段后半段如果主要是无看点内容，说明 end 太晚，必须提前
- 不能因为片段最短10秒就硬凑无效尾巴；短于10秒时只补必要前文，不补无效后文

错误示例：

爆点在100秒结束，100-120秒都是重复动画/普通讲解，却返回90-120。

正确示例：

返回92-103或95-105，保留爆点和关键反应即可。

尾部质量优先级高于片段完整度。宁可短一点，也不要让后半段没看点。

--------------------------------

高光密度规则：

如果一个爆分过程持续60秒：

可以拆分成多个高光片段，但只保留过程中的关键变化：

例如：

120-145
150-175
180-205

不要把整个60秒无差别全剪进去。

如果中间只是重复动画、数字缓慢增长、无新反应，可以跳过中间平淡部分。

如果一个长过程只有开头触发和结尾结果有价值：

- 分别返回开头触发段、结尾结果段
- 不要用一个长片段把中间无效过程连起来
- 不要让等待过程或重复动画成为片段主体

--------------------------------

输出格式：

只返回JSON。

[
  {
    "start": 110.5,
    "end": 145.0,
    "score": 95,
    "desc": "触发免费旋转"
  },
  {
    "start": 178.0,
    "end": 205.0,
    "score": 99,
    "desc": "爆出6700巨奖"
  }
]

字段说明：

start:
开始时间（秒）

end:
结束时间（秒）

end 必须是高光有效内容的结束点。

如果后面没有新奖励、新情绪、新机制、新结果，只是重复画面或无聊尾巴，不要包含。

片段尾部无效内容一般不得超过片段总时长的15%，更不能占到三分之一或一半。

score:
精彩程度
0-100

desc:
10字以内简短描述

返回结果按视频时间顺序排序。

不要按精彩程度排序。

不要为了覆盖全片而返回低价值片段。

如果视频中存在多个爽点过程，应保留最有剪辑价值的部分，而不是完整搬运。'''


def analyze_video(video_input, api_key, base_url, model, log_callback=None):
    candidates = [{"provider": "PRIMARY", "api_key": api_key, "base_url": base_url, "model": model}]
    return analyze_video_with_fallbacks(video_input, candidates, log_callback=log_callback)


def analyze_video_with_fallbacks(video_input, candidates, log_callback=None):
    if log_callback:
        log_callback("Gemini 分析视频中...")

    errors = []
    for index, candidate in enumerate(candidates, start=1):
        provider = candidate.get("provider") or f"候选{index}"
        api_key = candidate.get("api_key") or ""
        base_url = (candidate.get("base_url") or "").rstrip("/")
        model = candidate.get("model") or ""
        if not api_key or not base_url or not model:
            continue

        if log_callback:
            log_callback(f"尝试分析模型 [{provider}] {model} ({index}/{len(candidates)})")

        try:
            raw_text = _call_one_model(video_input, api_key, base_url, model, log_callback)
        except Exception as exc:
            message = f"[{provider}] {model}: {exc}"
            errors.append(message)
            if log_callback:
                log_callback(f"模型不可用，切换下一个: {message}")
            continue

        if log_callback:
            log_callback(f"Gemini 返回 ({provider}/{model}):\n{raw_text[:300]}...")

        return _parse_highlights(raw_text)

    error_text = "\n".join(errors[-6:]) if errors else "没有可用的 API Key/Base URL/Model 候选"
    raise RuntimeError(f"所有 Gemini 分析模型均不可用:\n{error_text}")


def _call_one_model(video_input, api_key, base_url, model, log_callback):
    if "generativelanguage.googleapis.com" in base_url:
        return _call_gemini_api(video_input, api_key, base_url, model, log_callback)
    return _call_openai_compatible_api(video_input, api_key, base_url, model, log_callback)


def _call_gemini_api(video_input, api_key, base_url, model, log_callback):
    url = f"{base_url}/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    if _is_url(str(video_input)):
        payload = _build_url_payload(video_input, HIGHLIGHT_PROMPT)
        if log_callback:
            log_callback(f"使用 URL 模式 (Gemini): {video_input}")
    else:
        video_path = Path(video_input)
        payload = _build_file_payload(video_path, HIGHLIGHT_PROMPT)
        size_mb = video_path.stat().st_size / 1024 / 1024
        if log_callback:
            log_callback(f"使用文件模式 (Gemini): {video_path.name} ({size_mb:.1f}MB)")

    resp = _post_once(url, headers=headers, payload=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 错误 ({resp.status_code}): {resp.text[:500]}")

    try:
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Gemini API 返回格式异常: {resp.text[:500]}") from exc


def _call_openai_compatible_api(video_input, api_key, base_url, model, log_callback):
    if _is_url(str(video_input)):
        try:
            return _call_openai_compatible_with_frames(video_input, api_key, base_url, model, log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"抽帧分析失败，回退 URL 文本模式: {exc}")

    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    if _is_url(str(video_input)):
        prompt_text = f"{HIGHLIGHT_PROMPT}\n\n视频链接: {video_input}"
        if log_callback:
            log_callback(f"使用 URL 文本模式 (OpenAI兼容): {video_input}")
    else:
        video_path = Path(video_input)
        prompt_text = HIGHLIGHT_PROMPT
        size_mb = video_path.stat().st_size / 1024 / 1024
        if log_callback:
            log_callback(f"使用文件文本模式 (OpenAI兼容): {video_path.name} ({size_mb:.1f}MB)")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 8000,
        "temperature": 0.2,
    }

    return _extract_openai_text(_post_once(url, headers=headers, payload=payload))


def _call_openai_compatible_with_frames(video_url, api_key, base_url, model, log_callback):
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        if log_callback:
            log_callback("下载临时视频用于抽帧分析...")
        video_path = _download_temp_video(video_url, tmp_dir)
        frames = _extract_frames(video_path, tmp_dir / "frames", FRAME_INTERVAL_SECONDS, MAX_ANALYSIS_FRAMES)
        if not frames:
            raise RuntimeError("未能抽取视频帧")

        if log_callback:
            log_callback(f"已按 {FRAME_INTERVAL_SECONDS}s 间隔抽取 {len(frames)} 帧，生成视频时间轴...")
        timeline_text = _request_timeline_with_frame_retries(url, headers, model, frames, log_callback)
        timeline = _parse_timeline(timeline_text)
        if log_callback:
            log_callback(f"视频时间轴:\n{json.dumps(timeline, ensure_ascii=False)[:800]}...")

        if log_callback:
            log_callback("基于时间轴二次筛选高光片段...")
        clips_text = _request_clips_from_timeline(url, headers, model, timeline)
        if log_callback:
            log_callback(f"二次筛选返回:\n{clips_text[:500]}...")
        return clips_text


def _is_url(s):
    return s.startswith("http://") or s.startswith("https://")


def _download_temp_video(video_url, tmp_dir):
    output_template = str(tmp_dir / "source.%(ext)s")
    ydl_opts = {
        "outtmpl": output_template,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 2,
        "extractor_retries": 2,
        "socket_timeout": 30,
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
    }
    node_path = Path("/Applications/Codex.app/Contents/Resources/node")
    if node_path.exists():
        ydl_opts["js_runtimes"] = {"node": {"path": str(node_path)}}
    if Path("/Applications/Google Chrome.app").exists():
        ydl_opts["cookiesfrombrowser"] = ("chrome",)
    elif Path("/Applications/Safari.app").exists():
        ydl_opts["cookiesfrombrowser"] = ("safari",)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(video_url, download=True)
    files = sorted(tmp_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("临时视频下载失败")
    return files[0]


def _extract_frames(video_path, frames_dir, interval_seconds=2, max_frames=80):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，无法抽帧")
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame_%04d.jpg"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval_seconds},scale=384:-1",
        "-frames:v",
        str(max_frames),
        "-q:v",
        "4",
        str(pattern),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    frames = []
    for index, path in enumerate(sorted(frames_dir.glob("frame_*.jpg"))):
        frames.append({"time": index * interval_seconds, "path": path})
    return frames


def _request_timeline_with_frame_retries(url, headers, model, frames, log_callback):
    attempts = [frames]
    if len(frames) > 8:
        attempts.append(frames[::2])
    if len(frames) > 4:
        attempts.append(frames[::3])

    last_error = None
    for attempt_frames in attempts:
        try:
            if log_callback:
                log_callback(f"提交 {len(attempt_frames)} 帧给模型生成时间轴...")
            return _request_timeline_from_frames(url, headers, model, attempt_frames)
        except Exception as exc:
            last_error = exc
            if log_callback:
                log_callback(f"{len(attempt_frames)} 帧时间轴请求失败: {exc}")
    raise RuntimeError(f"抽帧时间轴请求失败: {last_error}")


def _request_timeline_from_frames(url, headers, model, frames):
    content = [{"type": "text", "text": TIMELINE_PROMPT}]
    for frame in frames:
        content.append({"type": "text", "text": f"时间戳: {frame['time']}秒"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_image_to_base64(frame['path'])}"},
        })
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 12000,
        "temperature": 0.1,
    }
    return _extract_openai_text(_post_once(url, headers=headers, payload=payload, timeout=240))


def _request_clips_from_timeline(url, headers, model, timeline):
    timeline_json = json.dumps(timeline, ensure_ascii=False, indent=2)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": CLIP_SELECTION_PROMPT.replace("__TIMELINE_JSON__", timeline_json)}],
        "max_tokens": 4000,
        "temperature": 0.1,
    }
    return _extract_openai_text(_post_once(url, headers=headers, payload=payload, timeout=180))


def _image_to_base64(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _extract_openai_text(resp):
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 错误 ({resp.status_code}): {resp.text[:500]}")
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"OpenAI 兼容 API 返回格式异常: {resp.text[:500]}") from exc


def _parse_timeline(raw_text):
    obj = _parse_json_object(raw_text)
    if isinstance(obj, dict) and isinstance(obj.get("timeline"), list):
        return obj["timeline"]
    if isinstance(obj, list):
        return obj

    timeline_match = re.search(r'"timeline"\s*:\s*(\[[\s\S]*?\])\s*[,}]', raw_text)
    if timeline_match:
        try:
            timeline = json.loads(timeline_match.group(1))
            if isinstance(timeline, list):
                return timeline
        except json.JSONDecodeError:
            pass

    return [{"start": 0, "end": 0, "content": raw_text[:1000], "event": "原始返回", "value": "medium"}]


def _parse_json_object(raw_text):
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    text = re.sub(r"//.*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _post_once(url, headers, payload, timeout=180):
    return requests.post(url, headers=headers, json=payload, timeout=timeout)


def _build_url_payload(video_url, prompt):
    return {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"fileData": {"mimeType": "video/mp4", "fileUri": str(video_url)}}
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": 8000,
            "temperature": 0.2,
        }
    }


def _build_file_payload(video_path, prompt):
    import base64
    video_b64 = base64.b64encode(video_path.read_bytes()).decode()
    return {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "video/mp4", "data": video_b64}}
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": 8000,
            "temperature": 0.2,
        }
    }


def _parse_highlights(raw_text):
    parsed = _parse_json_object(raw_text)
    if isinstance(parsed, dict) and isinstance(parsed.get("clips"), list):
        return _normalize_highlights(parsed["clips"])
    if isinstance(parsed, list):
        return _normalize_highlights(parsed)

    json_match = re.search(r"\[[\s\S]*\]", raw_text)
    if not json_match:
        return []

    json_str = json_match.group(0)
    json_str = re.sub(r"//.*", "", json_str)
    json_str = re.sub(r",\s*\]", "]", json_str)
    json_str = re.sub(r",\s*}", "}", json_str)

    try:
        highlights = json.loads(json_str)
    except json.JSONDecodeError:
        json_str = _repair_truncated_json(json_str)
        try:
            highlights = json.loads(json_str)
        except json.JSONDecodeError:
            return []

    return _normalize_highlights(highlights)


def _normalize_highlights(highlights):
    candidates = []

    for h in highlights:
        if not isinstance(h, dict):
            continue
        try:
            start = max(0, float(h.get("start", 0)))
            end = float(h.get("end", 0))
            score = min(100, max(0, int(float(h.get("score", 0)))))
        except (TypeError, ValueError):
            continue
        desc = str(h.get("desc") or "高光").strip()[:30] or "高光"

        if start >= end or end - start < 1 or end - start > 120:
            continue

        candidates.append({"start": start, "end": end, "desc": desc, "score": score})

    candidates.sort(key=lambda x: (-x["score"], x["start"]))
    selected = []
    for item in candidates:
        item_duration = item["end"] - item["start"]
        duplicate = False
        for selected_item in selected:
            overlap_duration = min(item["end"], selected_item["end"]) - max(item["start"], selected_item["start"])
            if overlap_duration <= 0:
                continue
            selected_duration = selected_item["end"] - selected_item["start"]
            overlap_ratio = overlap_duration / max(1, min(item_duration, selected_duration))
            if overlap_ratio >= 0.5:
                duplicate = True
                break
        if not duplicate:
            selected.append(item)

    selected.sort(key=lambda x: x["start"])
    return selected


def _repair_truncated_json(json_str):
    json_str = json_str.rstrip()
    while json_str and json_str[-1] not in ("]", "}"):
        json_str = json_str[:-1]
    brace_count = json_str.count("{") - json_str.count("}")
    bracket_count = json_str.count("[") - json_str.count("]")
    json_str += "}" * brace_count + "]" * bracket_count
    return json_str
