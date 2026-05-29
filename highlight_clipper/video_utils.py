import os
import subprocess
import tempfile
from pathlib import Path


def get_video_info(video_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True)
    import json
    info = json.loads(result.stdout.decode("utf-8", errors="replace")) if result.returncode == 0 else {}
    fmt = info.get("format", {})
    duration = float(fmt.get("duration", 0))
    size = int(fmt.get("size", 0))
    return {"duration": duration, "size": size}


def compress_for_analysis(video_path, max_mb=15):
    info = get_video_info(video_path)
    size_mb = info["size"] / 1024 / 1024
    duration = info["duration"]

    if size_mb <= max_mb:
        return video_path

    target_bitrate = int(max_mb * 8 * 1024 / duration) if duration > 0 else 500

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    output_path = tmp.name

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "scale=640:-2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-b:v", f"{target_bitrate}k",
        "-c:a", "aac", "-b:a", "32k", "-ac", "1",
        "-movflags", "+faststart",
        "-y", output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    new_size = os.path.getsize(output_path) / 1024 / 1024
    return output_path


def clip_video(video_path, start_sec, end_sec, output_path, padding=1.5):
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
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def extract_audio(video_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{Path(video_path).stem}_audio.mp3"
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        str(audio_path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return audio_path
