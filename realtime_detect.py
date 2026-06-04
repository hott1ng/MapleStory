import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ultralytics import YOLO

from window_screenshot import (
    capture_window,
    enable_dpi_awareness,
    find_window_by_title,
    get_window_rect,
    list_windows,
    parse_hwnd,
    parse_interval,
)


DEFAULT_MODEL = Path("runs/detect/runs/maplestory_detect/weights/best.pt")
DEFAULT_PLAYER_Y_RATIO = 0.65


@dataclass
class Detection:
    bbox: list[float]
    confidence: float
    class_id: int
    class_name: str
    center: list[float]
    distance_from_center: float

    def to_dict(self) -> dict:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时检测窗口目标并输出 Detection 数据结构。")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--title", help="窗口标题，默认按包含关系匹配。")
    selector.add_argument("--hwnd", type=parse_hwnd, help="窗口句柄，支持十进制或 0x 开头的十六进制。")
    parser.add_argument("--exact", action="store_true", help="按标题精确匹配。")
    parser.add_argument("--interval", type=parse_interval, default=1.0, help="检测间隔秒数，默认 1 秒。")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="YOLO 模型路径。")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值，默认 0.25。")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 推理尺寸，默认 640。")
    parser.add_argument("--device", default="cpu", help="推理设备，默认 cpu；NVIDIA GPU 可用 0。")
    parser.add_argument("--player-x", type=float, help="人物中心 x 坐标；不填则使用窗口宽度中心。")
    parser.add_argument(
        "--player-y",
        type=float,
        help="人物中心 y 坐标；不填则使用窗口高度乘以 --player-y-ratio。",
    )
    parser.add_argument(
        "--player-y-ratio",
        type=float,
        default=DEFAULT_PLAYER_Y_RATIO,
        help="未指定 --player-y 时的人物 y 坐标比例，默认 0.65。",
    )
    parser.add_argument("--list", action="store_true", help="列出当前可见窗口后退出。")
    return parser.parse_args()


def fill_interactive_args(args: argparse.Namespace) -> None:
    if len(sys.argv) > 1 or args.list or args.hwnd is not None or args.title:
        return

    print("实时检测模式：请输入窗口标题或 hwnd。")
    print("例如：微信")
    value = input("窗口标题/hwnd: ").lstrip("\ufeff").strip()
    if not value:
        raise RuntimeError("没有输入窗口标题或 hwnd。")

    try:
        args.hwnd = parse_hwnd(value)
    except ValueError:
        args.title = value

    interval_value = input("检测间隔秒数，直接回车默认 1 秒: ").lstrip("\ufeff").strip()
    if interval_value:
        try:
            args.interval = parse_interval(interval_value)
        except (ValueError, argparse.ArgumentTypeError) as error:
            raise RuntimeError(f"检测间隔无效：{error}") from error


def resolve_target_hwnd(args: argparse.Namespace) -> int:
    if args.hwnd is not None:
        return args.hwnd
    if args.title:
        return find_window_by_title(args.title, args.exact)
    raise RuntimeError("请提供窗口标题或 hwnd。")


def get_player_center(args: argparse.Namespace, image_width: int, image_height: int) -> tuple[float, float]:
    player_x = args.player_x if args.player_x is not None else image_width / 2
    player_y = args.player_y if args.player_y is not None else image_height * args.player_y_ratio
    return player_x, player_y


def extract_detections(result, player_center: tuple[float, float]) -> list[Detection]:
    detections: list[Detection] = []
    names = result.names

    if result.boxes is None:
        return detections

    for box in result.boxes:
        bbox = [float(value) for value in box.xyxy[0].tolist()]
        x1, y1, x2, y2 = bbox
        confidence = float(box.conf[0])
        class_id = int(box.cls[0])
        class_name = names.get(class_id, f"class_{class_id}")
        center = [(x1 + x2) / 2, (y1 + y2) / 2]
        distance_from_center = math.dist(center, player_center)

        detections.append(
            Detection(
                bbox=bbox,
                confidence=confidence,
                class_id=class_id,
                class_name=class_name,
                center=center,
                distance_from_center=distance_from_center,
            )
        )

    detections.sort(key=lambda detection: detection.distance_from_center)
    return detections


def detect_once(
    hwnd: int,
    model: YOLO,
    args: argparse.Namespace,
) -> tuple[list[Detection], tuple[float, float], tuple[int, int]]:
    image = capture_window(hwnd)
    player_center = get_player_center(args, image.width, image.height)
    result = model.predict(
        source=image,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        verbose=False,
    )[0]
    return extract_detections(result, player_center), player_center, (image.width, image.height)


def print_detections(
    detections: list[Detection],
    player_center: tuple[float, float],
    window_size: tuple[int, int],
) -> None:
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(detections),
        "player_center": list(player_center),
        "window_size": list(window_size),
        "detections": [detection.to_dict() for detection in detections],
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    args = parse_args()
    enable_dpi_awareness()
    fill_interactive_args(args)

    if args.list:
        for hwnd, title in list_windows():
            print(f"hwnd={hwnd} title={title}")
        return

    model_path = Path(args.model)
    if not model_path.exists():
        raise RuntimeError(f"模型文件不存在: {model_path}")

    hwnd = resolve_target_hwnd(args)
    left, top, right, bottom = get_window_rect(hwnd)
    print(f"目标窗口 hwnd={hwnd}, size={right - left}x{bottom - top}")
    print(f"加载模型: {model_path.resolve()}")
    print(
        f"开始实时检测：interval={args.interval:g}s, conf={args.conf}, "
        f"imgsz={args.imgsz}, device={args.device}"
    )
    print("按 Ctrl+C 停止。")

    model = YOLO(str(model_path))
    while True:
        started_at = time.monotonic()
        detections, player_center, window_size = detect_once(hwnd, model, args)
        print_detections(detections, player_center, window_size)
        elapsed = time.monotonic() - started_at
        time.sleep(max(0.0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止实时检测。")
    except Exception as error:
        print(f"\n发生错误：{error}")
        if len(sys.argv) == 1:
            input("按回车退出...")
        raise SystemExit(1)
