import argparse
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from ultralytics import YOLO

from window_screenshot import (
    capture_window,
    enable_dpi_awareness,
    find_window_by_title,
    get_window_rect,
    list_windows,
    parse_hwnd,
)


DEFAULT_MODEL = Path("runs/detect/runs/maplestory_detect/weights/best.pt")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long


@dataclass
class Detection:
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[int, int]:
        return int((self.x1 + self.x2) / 2), int((self.y1 + self.y2) / 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时识别窗口中的 mogu，并用透明蒙版显示标框。")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--title", help="窗口标题，默认按包含关系匹配。")
    selector.add_argument("--hwnd", type=parse_hwnd, help="窗口句柄，支持十进制或 0x 开头的十六进制。")
    parser.add_argument("--exact", action="store_true", help="按标题精确匹配。")
    parser.add_argument("--interval", type=float, default=1.0, help="识别间隔秒数，默认 1 秒。")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="YOLO 模型路径。")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值，默认 0.25。")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 推理尺寸，默认 640。")
    parser.add_argument("--device", default="cpu", help="推理设备，默认 cpu；NVIDIA GPU 可用 0。")
    parser.add_argument("--list", action="store_true", help="列出当前可见窗口后退出。")
    return parser.parse_args()


def fill_interactive_args(args: argparse.Namespace) -> None:
    if len(sys.argv) > 1 or args.list or args.hwnd is not None or args.title:
        return

    print("实时识别模式：请输入窗口标题或 hwnd。")
    print("例如：微信")
    value = input("窗口标题/hwnd: ").lstrip("\ufeff").strip()
    if not value:
        raise RuntimeError("没有输入窗口标题或 hwnd。")

    try:
        args.hwnd = parse_hwnd(value)
    except ValueError:
        args.title = value


def resolve_target_hwnd(args: argparse.Namespace) -> int:
    if args.hwnd is not None:
        return args.hwnd
    if args.title:
        return find_window_by_title(args.title, args.exact)
    raise RuntimeError("请提供窗口标题或 hwnd。")


def make_window_click_through(widget: tk.Misc) -> None:
    widget.update_idletasks()
    hwnd = widget.winfo_id()
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def is_window_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_window_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def extract_detections(result) -> list[Detection]:
    detections: list[Detection] = []
    names = result.names

    if result.boxes is None:
        return detections

    for box in result.boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        confidence = float(box.conf[0])
        class_id = int(box.cls[0])
        class_name = names.get(class_id, f"class_{class_id}")
        detections.append(Detection(class_name, confidence, x1, y1, x2, y2))

    return detections


def detection_loop(
    hwnd: int,
    model: YOLO,
    args: argparse.Namespace,
    result_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        started_at = time.monotonic()

        if not is_window_alive(hwnd):
            put_latest(result_queue, RuntimeError("目标窗口已关闭。"))
            stop_event.set()
            return

        if is_window_minimized(hwnd):
            put_latest(result_queue, [])
            sleep_remaining(started_at, args.interval, stop_event)
            continue

        try:
            image = capture_window(hwnd)
            result = model.predict(
                source=image,
                conf=args.conf,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )[0]
            detections = extract_detections(result)
            print_detections(detections)
            put_latest(result_queue, detections)
        except Exception as error:
            put_latest(result_queue, error)

        sleep_remaining(started_at, args.interval, stop_event)


def put_latest(result_queue: queue.Queue, value) -> None:
    try:
        while True:
            result_queue.get_nowait()
    except queue.Empty:
        pass
    result_queue.put(value)


def sleep_remaining(started_at: float, interval: float, stop_event: threading.Event) -> None:
    elapsed = time.monotonic() - started_at
    stop_event.wait(max(0.0, interval - elapsed))


def print_detections(detections: list[Detection]) -> None:
    timestamp = time.strftime("%H:%M:%S")
    if not detections:
        print(f"[{timestamp}] mogu_count=0", flush=True)
        return

    parts = []
    for index, detection in enumerate(detections, start=1):
        center_x, center_y = detection.center
        parts.append(
            f"{index}: {detection.class_name} center=({center_x},{center_y}) "
            f"conf={detection.confidence:.2f}"
        )
    print(f"[{timestamp}] mogu_count={len(detections)} | " + " | ".join(parts), flush=True)


class DetectionOverlay:
    def __init__(
        self,
        hwnd: int,
        result_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        self.hwnd = hwnd
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.detections: list[Detection] = []
        self.parts: list[tk.Toplevel] = []
        self.last_signature = None

        self.root = tk.Tk()
        self.root.title("MapleStory realtime detection overlay")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _event: self.close())

    def run(self) -> None:
        self.update()
        self.root.mainloop()

    def close(self) -> None:
        self.stop_event.set()
        self.clear_parts()
        self.root.destroy()

    def update(self) -> None:
        if self.stop_event.is_set():
            self.root.destroy()
            return

        if not is_window_alive(self.hwnd):
            print("目标窗口已关闭，退出实时识别。")
            self.close()
            return

        self.consume_results()
        self.draw()
        self.root.after(100, self.update)

    def consume_results(self) -> None:
        try:
            while True:
                value = self.result_queue.get_nowait()
                if isinstance(value, Exception):
                    print(f"识别错误：{value}", flush=True)
                    continue
                self.detections = value
        except queue.Empty:
            pass

    def draw(self) -> None:
        if is_window_minimized(self.hwnd):
            self.clear_parts()
            return

        left, top, right, bottom = get_window_rect(self.hwnd)
        signature = (
            left,
            top,
            right,
            bottom,
            tuple(
                (
                    detection.class_name,
                    round(detection.confidence, 3),
                    int(detection.x1),
                    int(detection.y1),
                    int(detection.x2),
                    int(detection.y2),
                )
                for detection in self.detections
            ),
        )
        if signature == self.last_signature:
            return

        self.last_signature = signature
        self.clear_parts()

        for detection in self.detections:
            x1, y1, x2, y2 = map(int, (detection.x1, detection.y1, detection.x2, detection.y2))
            center_x, center_y = detection.center
            label = (
                f"{detection.class_name} {detection.confidence:.2f} "
                f"({center_x},{center_y})"
            )

            screen_x1 = left + x1
            screen_y1 = top + y1
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)

            self.add_block(screen_x1, screen_y1, width, 2, "#00ff3b")
            self.add_block(screen_x1, screen_y1 + height - 2, width, 2, "#00ff3b")
            self.add_block(screen_x1, screen_y1, 2, height, "#00ff3b")
            self.add_block(screen_x1 + width - 2, screen_y1, 2, height, "#00ff3b")
            self.add_block(left + center_x - 3, top + center_y - 3, 6, 6, "#ff2d2d")
            self.add_label(
                label,
                screen_x1 + 4,
                max(top, screen_y1 - 24),
            )

    def add_block(self, x: int, y: int, width: int, height: int, color: str) -> None:
        part = tk.Toplevel(self.root)
        part.overrideredirect(True)
        part.attributes("-topmost", True)
        part.configure(bg=color)
        part.geometry(f"{max(1, width)}x{max(1, height)}+{x}+{y}")
        make_window_click_through(part)
        self.parts.append(part)

    def add_label(self, text: str, x: int, y: int) -> None:
        part = tk.Toplevel(self.root)
        part.overrideredirect(True)
        part.attributes("-topmost", True)
        label = tk.Label(
            part,
            text=text,
            fg="#ffff00",
            bg="#202020",
            font=("Consolas", 11, "bold"),
            padx=3,
            pady=1,
        )
        label.pack()
        part.update_idletasks()
        part.geometry(f"{part.winfo_reqwidth()}x{part.winfo_reqheight()}+{x}+{y}")
        make_window_click_through(part)
        self.parts.append(part)

    def clear_parts(self) -> None:
        for part in self.parts:
            try:
                part.destroy()
            except tk.TclError:
                pass
        self.parts.clear()


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
    print(f"目标窗口 hwnd={hwnd}")
    print(f"加载模型: {model_path.resolve()}")
    model = YOLO(str(model_path))
    print(
        f"开始实时识别：interval={args.interval}, conf={args.conf}, "
        f"imgsz={args.imgsz}, device={args.device}"
    )
    print("按 Esc 关闭蒙版，或在控制台按 Ctrl+C 停止。")

    result_queue: queue.Queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=detection_loop,
        args=(hwnd, model, args, result_queue, stop_event),
        daemon=True,
    )
    worker.start()

    overlay = DetectionOverlay(hwnd, result_queue, stop_event)
    overlay.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止实时识别。")
    except Exception as error:
        print(f"\n发生错误：{error}")
        if len(sys.argv) == 1:
            input("按回车退出...")
        raise SystemExit(1)
