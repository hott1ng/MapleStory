import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_WATCH_PATTERNS = ("*.py", "*.qss", "*.ui")
IGNORED_DIR_NAMES = {".git", ".venv", "__pycache__", "build", "dist", "runs", "dataset"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="开发模式：监听文件变化并自动重启 GUI。")
    parser.add_argument("--entry", default="main.py", help="要启动的入口文件，默认 main.py。")
    parser.add_argument("--interval", type=float, default=0.8, help="文件扫描间隔秒数，默认 0.8。")
    return parser.parse_args()


def iter_watch_files(root: Path, patterns: Iterable[str]) -> Iterable[Path]:
    for pattern in patterns:
        for path in root.rglob(pattern):
            if any(part in IGNORED_DIR_NAMES for part in path.parts):
                continue
            if path.is_file():
                yield path


def snapshot_files(root: Path) -> dict[Path, int]:
    return {path: path.stat().st_mtime_ns for path in iter_watch_files(root, DEFAULT_WATCH_PATTERNS)}


def has_changes(previous: dict[Path, int], current: dict[Path, int]) -> bool:
    return previous != current


def start_process(entry: Path) -> subprocess.Popen:
    print(f"[dev] 启动 GUI: {entry}", flush=True)
    return subprocess.Popen([sys.executable, str(entry)])


def stop_process(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return

    print("[dev] 检测到文件变化，重启 GUI。", flush=True)
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent
    entry = (root / args.entry).resolve()
    if not entry.exists():
        raise FileNotFoundError(f"入口文件不存在: {entry}")

    last_snapshot = snapshot_files(root)
    process = start_process(entry)

    try:
        while True:
            time.sleep(args.interval)
            current_snapshot = snapshot_files(root)
            if has_changes(last_snapshot, current_snapshot):
                last_snapshot = current_snapshot
                stop_process(process)
                process = start_process(entry)
            elif process.poll() is not None:
                print("[dev] GUI 已退出，等待下一次文件变化。", flush=True)
                process = None
    except KeyboardInterrupt:
        print("\n[dev] 停止热调试。", flush=True)
    finally:
        stop_process(process)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[dev] 发生错误：{error}", flush=True)
        raise SystemExit(1)
