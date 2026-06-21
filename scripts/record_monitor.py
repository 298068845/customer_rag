from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
import mss


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--monitor", type=int, default=2)
    parser.add_argument("--fps", type=int, default=20)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with mss.mss() as capture:
        monitor = capture.monitors[args.monitor]
        width = int(monitor["width"])
        height = int(monitor["height"])
        command = [
            ffmpeg,
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgra",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(args.fps),
            "-i",
            "-",
            "-vf",
            "scale=1920:1080",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(args.output),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        assert process.stdin is not None
        frame_interval = 1.0 / args.fps
        deadline = time.perf_counter() + args.duration
        next_frame = time.perf_counter()
        try:
            while time.perf_counter() < deadline:
                frame = capture.grab(monitor)
                process.stdin.write(frame.raw)
                next_frame += frame_interval
                delay = next_frame - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
        finally:
            process.stdin.close()
            process.wait(timeout=30)


if __name__ == "__main__":
    main()
