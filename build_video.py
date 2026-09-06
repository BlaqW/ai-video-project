#!/usr/bin/env python3
"""Build the 15-second anonymous compliance-desk documentary insert.

The only runtime dependency is FFmpeg (including ffprobe and libx264).  All
motion, grading, texture, sound design, assembly, and validation are expressed
as deterministic FFmpeg commands so the project remains easy to reproduce.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "assets"
OUTPUT_DIR = ROOT / "output"
DEBUG_DIR = OUTPUT_DIR / "debug"
FINAL = OUTPUT_DIR / "compliance_final.mp4"
WIDTH, HEIGHT, FPS = 1920, 1080, 30
SHOT_FRAMES = 90


def natural_key(path: Path) -> list[object]:
    """Sort numbered filenames naturally while retaining stable name order."""
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", path.name)]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def require_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        missing = [name for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not path]
        raise SystemExit(
            "Missing required executable(s): " + ", ".join(missing) + ". "
            "Install an FFmpeg build with ffprobe and the libx264 encoder, then rerun "
            "`python3 build_video.py`."
        )
    return ffmpeg, ffprobe


def discover_images() -> list[Path]:
    extensions = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
    images = sorted(
        (path for path in ASSET_DIR.iterdir() if path.is_file() and path.suffix.lower() in extensions),
        key=natural_key,
    )
    if not images:
        raise SystemExit(f"No scene images found in {ASSET_DIR}")
    return images


def motion_filter(index: int) -> str:
    # A generous overscan permits tiny eased push-ins/reframes without exposing edges.
    # zoompan's on-counter makes every move deterministic and exactly 90 frames long.
    moves = [
        ("1.035+0.035*(1-cos(PI*on/89))/2", "(iw-iw/zoom)*0.40", "(ih-ih/zoom)*0.44"),
        ("1.055+0.025*(1-cos(PI*on/89))/2", "(iw-iw/zoom)*(0.56-0.05*on/89)", "(ih-ih/zoom)*0.48"),
        ("1.105+0.030*(1-cos(PI*on/89))/2", "(iw-iw/zoom)*(0.54+0.03*on/89)", "(ih-ih/zoom)*0.57"),
        ("1.075+0.022*(1-cos(PI*on/89))/2", "(iw-iw/zoom)*(0.39+0.05*on/89)", "(ih-ih/zoom)*0.42"),
        ("1.090+0.012*(1-cos(PI*on/89))/2", "(iw-iw/zoom)*0.51", "(ih-ih/zoom)*(0.51-0.025*on/89)"),
    ]
    zoom, x, y = moves[index]
    return (
        f"scale=2200:1238:force_original_aspect_ratio=increase,crop=2200:1238,"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={SHOT_FRAMES}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        # Restrained cool shadows / warm practicals, gentle contrast, and optical falloff.
        "curves=all='0/0 0.18/0.145 0.50/0.49 0.82/0.86 1/0.985',"
        "colorbalance=bs=0.035:gs=0.010:rh=0.025:gh=0.008,"
        "eq=saturation=0.88:contrast=1.055:brightness=-0.008,"
        "vignette=PI/5.3:eval=frame,noise=alls=2.0:allf=t+u,format=yuv420p"
    )


def build_shots(ffmpeg: str, images: list[Path]) -> list[Path]:
    # Story: establish, page turn, detail, cross-check, then a held tense pause.
    image_order = [images[0], images[min(1, len(images) - 1)], images[min(2, len(images) - 1)],
                   images[0], images[min(2, len(images) - 1)]]
    shots: list[Path] = []
    for index, image_path in enumerate(image_order):
        shot = DEBUG_DIR / f"shot_{index + 1:02d}.mp4"
        run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-loop", "1",
            "-i", str(image_path), "-vf", motion_filter(index), "-frames:v", str(SHOT_FRAMES),
            "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-movflags", "+faststart", str(shot),
        ])
        shots.append(shot)
    return shots


def build_audio(ffmpeg: str) -> Path:
    ambience = DEBUG_DIR / "ambience.wav"
    # Filtered noise supplies HVAC and low tension; brief shaped noise gestures imply
    # paper/desk contact around the page turn, inspection, cross-check, and final pause.
    audio_filter = (
        "anoisesrc=color=pink:amplitude=0.018:duration=15:sample_rate=48000[room];"
        "sine=frequency=48:duration=15:sample_rate=48000,volume=0.012,lowpass=f=90[rumble];"
        "anoisesrc=color=white:amplitude=0.035:duration=15:sample_rate=48000,"
        "highpass=f=650,lowpass=f=4200,"
        "volume='if(between(t,3.05,3.38),0.24*sin(PI*(t-3.05)/0.33),"
        "if(between(t,6.15,6.30),0.13*sin(PI*(t-6.15)/0.15),"
        "if(between(t,9.10,9.24),0.09*sin(PI*(t-9.10)/0.14),"
        "if(between(t,12.08,12.30),0.15*sin(PI*(t-12.08)/0.22),0))))':eval=frame[paper];"
        "[room][rumble][paper]amix=inputs=3:normalize=0,highpass=f=28,lowpass=f=7000,"
        "acompressor=threshold=0.08:ratio=2:attack=20:release=250,"
        "afade=t=in:st=0:d=1.2,afade=t=out:st=13.8:d=1.2,alimiter=limit=0.35[a]"
    )
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
         "-filter_complex", audio_filter,
         "-map", "[a]", "-t", "15",
         "-c:a", "pcm_s16le", str(ambience)])
    return ambience


def assemble(ffmpeg: str, shots: list[Path], ambience: Path) -> None:
    concat_file = DEBUG_DIR / "shots.ffconcat"
    concat_file.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.as_posix()}'\n" for p in shots))
    silent_master = DEBUG_DIR / "picture_master.mp4"
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", str(silent_master)])
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(silent_master),
         "-i", str(ambience), "-map", "0:v:0", "-map", "1:a:0", "-frames:v", "450", "-t", "15",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(FINAL)])


def verify(ffprobe: str) -> dict[str, object]:
    probe = subprocess.run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,nb_frames",
        "-of", "json", str(FINAL),
    ], check=True, capture_output=True, text=True)
    metadata = json.loads(probe.stdout)
    (DEBUG_DIR / "ffprobe.json").write_text(json.dumps(metadata, indent=2) + "\n")
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    duration = float(metadata["format"]["duration"])
    checks = {
        "duration": abs(duration - 15.0) < 0.001,
        "resolution": (video.get("width"), video.get("height")) == (WIDTH, HEIGHT),
        "fps": video.get("r_frame_rate") == "30/1",
        "codec": video.get("codec_name") == "h264",
        "pixel_format": video.get("pix_fmt") == "yuv420p",
        "frames": int(video.get("nb_frames", 0)) == 450,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Final validation failed: {checks}\n{json.dumps(metadata, indent=2)}")
    return metadata


def main() -> None:
    ffmpeg, ffprobe = require_tools()
    OUTPUT_DIR.mkdir(exist_ok=True)
    DEBUG_DIR.mkdir(exist_ok=True)
    images = discover_images()
    print("Scene order:", *(path.name for path in images), sep="\n  ")
    shots = build_shots(ffmpeg, images)
    ambience = build_audio(ffmpeg)
    assemble(ffmpeg, shots, ambience)
    metadata = verify(ffprobe)
    print("Verified final:", FINAL)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"Render command failed with exit code {error.returncode}.", file=sys.stderr)
        raise SystemExit(error.returncode) from error
