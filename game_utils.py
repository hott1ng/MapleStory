import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


STARTFILE_SUFFIXES = {".lnk", ".url"}


@dataclass
class LaunchResult:
    game_path: Path
    working_dir: Path
    method: str
    process_id: Optional[int] = None


def resolve_file_path(file_path: str) -> Path:
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"路径不是文件: {path}")
    return path.resolve()


def resolve_working_dir(game_path: Path, working_dir: Optional[str] = None) -> Path:
    if working_dir:
        path = Path(working_dir).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"工作目录不存在: {path}")
        if not path.is_dir():
            raise ValueError(f"工作目录不是文件夹: {path}")
        return path.resolve()

    return game_path.parent


def launch_game(
    game_path: str,
    launch_args: Optional[Sequence[str]] = None,
    working_dir: Optional[str] = None,
    use_startfile: bool = False,
) -> LaunchResult:
    resolved_game_path = resolve_file_path(game_path)
    resolved_working_dir = resolve_working_dir(resolved_game_path, working_dir)
    args = list(launch_args or [])

    if use_startfile or resolved_game_path.suffix.lower() in STARTFILE_SUFFIXES:
        if args:
            raise ValueError("使用 startfile 启动时暂不支持附加启动参数。")
        os.startfile(str(resolved_game_path))  # type: ignore[attr-defined]
        return LaunchResult(
            game_path=resolved_game_path,
            working_dir=resolved_working_dir,
            method="startfile",
        )

    process = subprocess.Popen(
        [str(resolved_game_path), *args],
        cwd=str(resolved_working_dir),
    )
    return LaunchResult(
        game_path=resolved_game_path,
        working_dir=resolved_working_dir,
        method="subprocess",
        process_id=process.pid,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据游戏路径启动游戏。")
    parser.add_argument("game_path", nargs="?", help="游戏 exe、快捷方式或启动器路径。")
    parser.add_argument("--cwd", help="启动时使用的工作目录，默认使用游戏文件所在目录。")
    parser.add_argument("--startfile", action="store_true", help="使用 Windows 默认方式打开文件。")
    parser.add_argument("launch_args", nargs=argparse.REMAINDER, help="传给游戏进程的附加参数。")
    return parser.parse_args()


def fill_interactive_args(args: argparse.Namespace) -> None:
    if len(sys.argv) > 1 and args.game_path:
        return

    value = input("游戏路径: ").lstrip("\ufeff").strip().strip('"')
    if not value:
        raise RuntimeError("没有输入游戏路径。")
    args.game_path = value


def main() -> None:
    args = parse_args()
    fill_interactive_args(args)
    result = launch_game(
        game_path=args.game_path,
        launch_args=args.launch_args,
        working_dir=args.cwd,
        use_startfile=args.startfile,
    )

    print(f"已启动: {result.game_path}")
    print(f"工作目录: {result.working_dir}")
    print(f"启动方式: {result.method}")
    if result.process_id is not None:
        print(f"进程 PID: {result.process_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\n发生错误：{error}")
        if len(sys.argv) == 1:
            input("按回车退出...")
        raise SystemExit(1)
