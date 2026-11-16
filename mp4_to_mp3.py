#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mp4_to_mp3.py —— 把 MP4（以及 mov/mkv/webm 等常见视频格式）批量转成 MP3。

依赖：ffmpeg。脚本会自动查找：
  1. 脚本同目录下的 ffmpeg.exe / ffmpeg\\bin\\ffmpeg.exe
  2. 环境变量 FFMPEG_DIR 指向的目录
  3. 系统 PATH
  4. winget 安装的 ffmpeg、C:\\ffmpeg\\bin

用法示例：
    python mp4_to_mp3.py 视频.mp4
    python mp4_to_mp3.py a.mp4 b.mp4 -o D:\\音乐
    python mp4_to_mp3.py 视频文件夹 --bitrate 320k
    python mp4_to_mp3.py 视频.mp4 --track 2          # 选第 2 条音轨
    python mp4_to_mp3.py 视频.mp4 --quality 0        # VBR 最高质量

把文件拖到 转换.bat 上也能用。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm",
    ".flv", ".ts", ".wmv", ".mpg", ".mpeg", ".3gp",
}

IS_TTY = bool(getattr(sys.stdout, "isatty", None) and sys.stdout.isatty())

# 在无控制台的进程里（pythonw / 打包后的窗口程序）启动 ffmpeg 时不要弹黑框
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
BAR_WIDTH = 24


# ---------------------------------------------------------------- 工具查找

def _probe_dir(d: Path) -> Path | None:
    """如果 d 里（或 d/bin 里）有 ffmpeg.exe，返回可执行文件路径。"""
    for cand in (d / "ffmpeg.exe", d / "bin" / "ffmpeg.exe", d):
        if cand.is_file() and cand.name.lower() == "ffmpeg.exe":
            return cand
    return None


def find_ffmpeg() -> Path:
    """找到 ffmpeg.exe。找不到时抛出 SystemExit。"""
    exe = None

    env_dir = os.environ.get("FFMPEG_DIR")
    if env_dir:
        exe = _probe_dir(Path(env_dir))

    if exe is None:
        bases: list[Path] = []
        if getattr(sys, "frozen", False):
            # 打包成 exe 后，优先找 exe 所在目录（以及 PyInstaller 的解包目录）
            bases.append(Path(sys.executable).resolve().parent)
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                bases.append(Path(meipass))
        bases.append(Path(__file__).resolve().parent)
        for base in bases:
            for d in (base, base / "ffmpeg", base / "bin"):
                exe = _probe_dir(d)
                if exe:
                    break
            if exe:
                break

    if exe is None:
        found = shutil.which("ffmpeg")
        if found:
            exe = Path(found)

    if exe is None:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            pattern = Path(local) / "Microsoft" / "WinGet" / "Packages"
            if pattern.is_dir():
                for pkg in pattern.glob("Gyan.FFmpeg*"):
                    for f in pkg.rglob("ffmpeg.exe"):
                        exe = f
                        break
                    if exe:
                        break

    if exe is None:
        exe = _probe_dir(Path(r"C:\ffmpeg"))

    if exe is None:
        sys.exit(
            "找不到 ffmpeg。\n"
            "安装方法（任选其一）：\n"
            "  1. 在终端执行：winget install Gyan.FFmpeg\n"
            "  2. 下载静态包 https://www.gyan.dev/ffmpeg/builds/ ，解压后把 ffmpeg.exe\n"
            "     放到程序旁边（或 ffmpeg\\bin 子目录里），或把 bin 目录加到 PATH\n"
            "装好后重新运行即可。"
        )

    return exe


# ---------------------------------------------------------------- 探测信息

DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio: ([A-Za-z0-9_]+)")


def probe_media(ffmpeg: Path, src: Path) -> tuple[float | None, list[str]]:
    """用 `ffmpeg -i` 的输出解析时长度与音轨列表。返回 (秒或 None, [编码名...])。"""
    try:
        p = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(src)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, creationflags=_NO_WINDOW,
        )
    except Exception:
        return None, []
    text = p.stderr or ""
    duration = None
    m = DUR_RE.search(text)
    if m:
        h, mi, s = m.groups()
        duration = int(h) * 3600 + int(mi) * 60 + float(s)
    tracks = AUDIO_RE.findall(text)
    return duration, tracks


# ---------------------------------------------------------------- 进度条

def human_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def fmt_time(sec: float) -> str:
    sec = int(max(sec, 0))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def draw_bar(pct: float | None, cur: float, total: float | None) -> str:
    if pct is None:
        return f"  {fmt_time(cur)}     "
    pct = min(max(pct, 0.0), 1.0)
    filled = int(pct * BAR_WIDTH)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    tail = f" {fmt_time(cur)}/{fmt_time(total)}" if total else f" {fmt_time(cur)}"
    return f" [{bar}] {pct * 100:5.1f}%{tail}"


# ---------------------------------------------------------------- 转换

TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")


def convert_file(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    *,
    duration: float | None = None,
    track: int = 0,
    bitrate: str | None = None,
    quality: int = 2,
    overwrite: bool = False,
    on_progress: Callable[[float | None, float, float | None], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """调用 ffmpeg 转换一个文件，返回 (是否成功, 错误信息)。

    on_progress(百分比或 None, 已转秒数, 总秒数) 会周期性回调；
    on_log(整行文本) 接收 ffmpeg 的错误输出，便于界面展示。
    """
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin",
        "-progress", "pipe:1", "-nostats", "-loglevel", "error",
        "-i", str(src),
        "-map", f"0:a:{track}",
        "-vn",
        "-map_metadata", "0",
        "-c:a", "libmp3lame",
        "-id3v2_version", "3",
        "-write_id3v1", "1",
    ]
    if bitrate:
        cmd += ["-b:a", bitrate]
    else:
        cmd += ["-q:a", str(quality)]
    cmd += [("-y" if overwrite else "-n"), str(dst)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=_NO_WINDOW,
    )

    errors: list[str] = []
    cur = 0.0

    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            m = TIME_RE.search(line)
            if m:
                us = int(m.group(1))
                # out_time_ms 在部分版本里实际也是微秒，统一按微秒算
                cur = us / 1_000_000
                pct = (cur / duration) if duration else None
                if on_progress:
                    on_progress(pct, cur, duration)
            elif line.startswith("progress=") or (
                "=" in line and re.match(r"^[a-z_]+=", line)
            ):
                continue
            else:
                errors.append(line)
                if on_log:
                    on_log(line)

        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise

    if code != 0:
        return False, (errors[0] if errors else f"ffmpeg 退出码 {code}")
    if not dst.exists():
        return False, "没有生成输出文件"
    return True, ""


def convert_one(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    duration: float | None,
    track: int,
    bitrate: str | None,
    quality: int,
    overwrite: bool,
) -> bool:
    """命令行用的单文件转换：打印进度和结果。"""
    def _progress(pct: float | None, cur: float, total: float | None) -> None:
        if IS_TTY:
            sys.stdout.write("\r" + draw_bar(pct, cur, total))
            sys.stdout.flush()

    ok, msg = convert_file(
        ffmpeg, src, dst, duration=duration, track=track,
        bitrate=bitrate, quality=quality, overwrite=overwrite,
        on_progress=_progress,
    )
    if IS_TTY:
        sys.stdout.write("\r" + " " * 70 + "\r")
    if not ok:
        print(f"    失败：{msg}")
        return False
    print(f"    完成：{dst.name}  ({human_size(dst.stat().st_size)})")
    return True


# ---------------------------------------------------------------- 主流程

def expand_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                    files.append(f)
        elif p.is_file():
            files.append(p)
        else:
            print(f"跳过（找不到）：{raw}", file=sys.stderr)
    # 去重但保持顺序
    seen, out = set(), []
    for f in files:
        k = f.resolve()
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def target_path(src: Path, out_dir: Path | None, force: bool) -> Path | None:
    base = out_dir if out_dir else src.parent
    dst = base / (src.stem + ".mp3")
    if dst.exists() and not force:
        return None
    return dst


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        prog="mp4_to_mp3.py",
        description="把 MP4 等视频文件转成 MP3。支持拖放、批量、目录递归。",
    )
    ap.add_argument("inputs", nargs="*", help="视频文件或文件夹")
    ap.add_argument("-o", "--out-dir", help="输出目录（默认放在原文件旁边）")
    ap.add_argument("-b", "--bitrate", help="固定码率，如 128k / 192k / 320k")
    ap.add_argument(
        "-q", "--quality", type=int, default=2,
        help="VBR 质量 0-9，数字越小越好（默认 2，约 190kbps 的听感）",
    )
    ap.add_argument("-t", "--track", type=int, default=0,
                    help="选第几条音轨，从 0 开始（默认 0，即第一条）")
    ap.add_argument("-f", "--force", action="store_true", help="覆盖已存在的 MP3")
    ap.add_argument("--ffmpeg", help="手动指定 ffmpeg 可执行文件路径")

    args = ap.parse_args(argv)

    if not args.inputs:
        ap.print_help()
        print("\n提示：把视频文件拖到 转换.bat 上，或运行 python mp4_to_mp3.py 视频.mp4")
        return 1

    if not (0 <= args.quality <= 9):
        ap.error("--quality 需要在 0-9 之间")

    ffmpeg = Path(args.ffmpeg) if args.ffmpeg else find_ffmpeg()
    if not ffmpeg.is_file():
        sys.exit(f"找不到指定的 ffmpeg：{ffmpeg}")

    files = expand_inputs(args.inputs)
    if not files:
        print("没有可转换的视频文件。", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else None
    if out_dir and not out_dir.exists():
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"ffmpeg: {ffmpeg}")
    print(f"待处理 {len(files)} 个文件，编码参数："
          f"{args.bitrate + ' CBR' if args.bitrate else f'VBR q={args.quality}'}\n")

    ok = skipped = failed = 0
    started = time.time()

    for i, src in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {src.name}")

        duration, tracks = probe_media(ffmpeg, src)
        if not tracks:
            print("    跳过：没有音轨（或文件无法解析）")
            skipped += 1
            continue
        if args.track >= len(tracks):
            print(f"    跳过：只有 {len(tracks)} 条音轨，找不到第 {args.track} 条")
            skipped += 1
            continue

        dst = target_path(src, out_dir, args.force)
        if dst is None:
            print("    跳过：同名 MP3 已存在（加 --force 覆盖）")
            skipped += 1
            continue

        if convert_one(ffmpeg, src, dst, duration, args.track,
                       args.bitrate, args.quality, args.force):
            ok += 1
        else:
            failed += 1

    elapsed = time.time() - started
    print(f"\n完成：成功 {ok}，跳过 {skipped}，失败 {failed}，"
          f"耗时 {elapsed:.1f} 秒")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
