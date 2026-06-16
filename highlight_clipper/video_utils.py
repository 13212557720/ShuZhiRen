import json
import subprocess


def get_video_info(video_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffprobe 读取视频信息失败: {error or video_path}")
    info = json.loads(result.stdout.decode("utf-8", errors="replace"))
    fmt = info.get("format", {})
    duration = float(fmt.get("duration", 0))
    size = int(fmt.get("size", 0))
    return {"duration": duration, "size": size}


def clip_video(video_path, start_sec, end_sec, output_path, padding=1.5):
    if end_sec <= start_sec:
        raise ValueError("剪辑结束时间必须大于开始时间")

    clip_start = max(0, start_sec - padding)
    duration = end_sec - start_sec + padding * 2
    duration = max(1, duration)

    cmd = [
        "ffmpeg", "-ss", str(clip_start), "-i", str(video_path),
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac",
        "-avoid_negative_ts", "make_zero",
        "-y", str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg 剪辑失败: {error or output_path}")
    return output_path
