import re
import json
import requests
from pathlib import Path

HIGHLIGHT_PROMPT ='''你是一名专业的短视频游戏剪辑师。

任务：
分析整个视频，找出适合做短视频二创剪辑的高光片段，并返回时间轴。

核心目标：

不是寻找唯一最炸裂瞬间。

而是寻找能够让观众持续看下去的多个爽点节点。

重点保留：

- 抽奖开始前的期待感
- 特殊模式触发
- 连续中奖过程
- 奖励不断升级过程
- 巨奖爆发过程
- 主播激动反应
- 奖励结算展示

避免：

- 长时间等待
- 无奖励旋转
- 重复动画
- 无解说空镜头
- 重复性质的小奖

--------------------------------

片段数量规则：

根据视频总时长动态决定：

0-3分钟：
返回3-5个片段

3-5分钟：
返回4-7个片段

5-10分钟：
返回6-10个片段

10分钟以上：
返回8-15个片段

要求高光覆盖整个视频流程。

不要只集中在最后几分钟。

--------------------------------

老虎机/抽奖游戏特别规则：

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

C级事件（可忽略）

- 普通中奖
- 小额奖励
- 无变化旋转

--------------------------------

关于主播介绍类视频：

如果视频开头是：

- 主播介绍游戏
- 展示手机
- 讲解规则

当第一次点击开始抽奖时：

视为游戏正式开始。

从这里开始寻找高光。

除非主播出现极强情绪反应，否则介绍内容一般不保留。

--------------------------------

时间轴规则：

每个片段长度：

最短10秒
最长40秒

原则：

向前补足上下文：

例如BONUS在120秒触发，

可以返回：

110-140

而不是：

120-122

确保观众能看懂发生了什么。

--------------------------------

高光密度规则：

如果一个爆分过程持续60秒：

允许拆分成多个高光片段：

例如：

120-145
150-175
180-205

不要因为属于同一次爆分就只保留一个片段。

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

score:
精彩程度
0-100

desc:
10字以内简短描述

返回结果按视频时间顺序排序。

不要按精彩程度排序。

不要遗漏中后段高光。

如果视频中存在多个爽点过程，应尽量完整覆盖。'''


def analyze_video(video_input, api_key, base_url, model, log_callback=None):
    if log_callback:
        log_callback("Gemini 分析视频中...")

    base_url = base_url.rstrip("/")
    if "generativelanguage.googleapis.com" in base_url:
        raw_text = _call_gemini_api(video_input, api_key, base_url, model, log_callback)
    else:
        raw_text = _call_openai_compatible_api(video_input, api_key, base_url, model, log_callback)

    if log_callback:
        log_callback(f"Gemini 返回:\n{raw_text[:300]}...")

    return _parse_highlights(raw_text)


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

    resp = requests.post(url, headers=headers, json=payload, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 错误 ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_compatible_api(video_input, api_key, base_url, model, log_callback):
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    if _is_url(str(video_input)):
        prompt_text = f"{HIGHLIGHT_PROMPT}\n\n视频链接: {video_input}"
        if log_callback:
            log_callback(f"使用 URL 模式 (OpenAI兼容): {video_input}")
    else:
        video_path = Path(video_input)
        prompt_text = HIGHLIGHT_PROMPT
        size_mb = video_path.stat().st_size / 1024 / 1024
        if log_callback:
            log_callback(f"使用文件模式 (OpenAI兼容): {video_path.name} ({size_mb:.1f}MB)")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 8000,
        "temperature": 0.2,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 错误 ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _is_url(s):
    return s.startswith("http://") or s.startswith("https://")


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

    result = []
    used_ranges = []

    for h in highlights:
        start = float(h.get("start", 0))
        end = float(h.get("end", 0))
        desc = str(h.get("desc", "")).strip()
        score = int(h.get("score", 0))

        if start >= end or end - start < 1 or end - start > 120:
            continue

        heavy_overlap = False
        for us, ue in used_ranges:
            overlap_duration = min(end, ue) - max(start, us)
            if overlap_duration > 5:
                heavy_overlap = True
                break
        if heavy_overlap:
            continue

        used_ranges.append((start, end))
        result.append({"start": start, "end": end, "desc": desc, "score": score})

    result.sort(key=lambda x: x["start"])
    return result


def _repair_truncated_json(json_str):
    json_str = json_str.rstrip()
    while json_str and json_str[-1] not in ("]", "}"):
        json_str = json_str[:-1]
    brace_count = json_str.count("{") - json_str.count("}")
    bracket_count = json_str.count("[") - json_str.count("]")
    json_str += "}" * brace_count + "]" * bracket_count
    return json_str
