import argparse
import ctypes
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from time import sleep

from PIL import Image, ImageGrab


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
kernel32.GetConsoleWindow.argtypes = []
kernel32.GetConsoleWindow.restype = wintypes.HWND


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDPIAware()
    except AttributeError:
        pass


def get_window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def get_console_window() -> int:
    return kernel32.GetConsoleWindow()


def is_current_command_window(window_title: str) -> bool:
    current_exe = Path(sys.argv[0]).name.casefold()
    title = window_title.casefold()
    return current_exe in title and "--title" in title


def list_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            title = get_window_title(hwnd)
            if title:
                windows.append((hwnd, title))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def find_window_by_title(title: str, exact: bool) -> int:
    needle = title.casefold()
    console_hwnd = get_console_window()
    exact_matches = []
    partial_matches = []

    for hwnd, window_title in list_windows():
        if hwnd == console_hwnd or is_current_command_window(window_title):
            continue
        haystack = window_title.casefold()
        if haystack == needle:
            exact_matches.append((hwnd, window_title))
        elif not exact and needle in haystack:
            partial_matches.append((hwnd, window_title))

    matches = exact_matches or partial_matches

    if not matches:
        raise RuntimeError(f"没有找到标题匹配的窗口: {title!r}")

    if len(matches) > 1:
        print("找到多个匹配窗口，默认使用第一个；完全匹配会优先于包含匹配：")
        for hwnd, window_title in matches:
            print(f"  hwnd={hwnd} title={window_title}")

    return matches[0][0]


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())

    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError(f"窗口尺寸无效: hwnd={hwnd}")

    return rect.left, rect.top, rect.right, rect.bottom


def capture_window(hwnd: int) -> Image.Image:
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)

    try:
        printed = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        if not printed:
            return ImageGrab.grab(bbox=(left, top, right, bottom))

        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
        )
        if lines == 0:
            raise ctypes.WinError(ctypes.get_last_error())

        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def build_output_path(output_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return output_dir / f"{prefix}_{timestamp}.png"


def parse_hwnd(value: str) -> int:
    return int(value, 0)


def parse_interval(value: str) -> float:
    interval = float(value)
    if interval <= 0:
        raise argparse.ArgumentTypeError("截图间隔必须大于 0 秒。")
    return interval


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按窗口标题或句柄定时截取窗口截图。")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--title", help="窗口标题，默认按包含关系匹配。")
    selector.add_argument("--hwnd", type=parse_hwnd, help="窗口句柄，支持十进制或 0x 开头的十六进制。")
    parser.add_argument("--exact", action="store_true", help="按标题精确匹配。")
    parser.add_argument("--interval", type=parse_interval, default=1.0, help="截图间隔秒数，默认 1 秒。")
    parser.add_argument("--output", default="screenshots", help="截图输出目录，默认 screenshots。")
    parser.add_argument("--prefix", default="window", help="截图文件名前缀，默认 window。")
    parser.add_argument("--list", action="store_true", help="列出当前可见窗口后退出。")
    return parser.parse_args()


def fill_interactive_args(args: argparse.Namespace) -> None:
    if len(sys.argv) > 1 or args.list or args.hwnd is not None or args.title:
        return

    print("双击模式：请输入窗口标题或 hwnd，以及截图间隔秒数。")
    print("例如：微信")
    value = input("窗口标题/hwnd: ").lstrip("\ufeff").strip()
    if not value:
        raise RuntimeError("没有输入窗口标题或 hwnd。")

    try:
        args.hwnd = parse_hwnd(value)
    except ValueError:
        args.title = value

    interval_value = input("截图间隔秒数，直接回车默认 1 秒: ").lstrip("\ufeff").strip()
    if interval_value:
        try:
            args.interval = parse_interval(interval_value)
        except (ValueError, argparse.ArgumentTypeError) as error:
            raise RuntimeError(f"截图间隔无效：{error}") from error


def main() -> None:
    args = parse_args()
    enable_dpi_awareness()
    fill_interactive_args(args)

    if args.list:
        for hwnd, title in list_windows():
            print(f"hwnd={hwnd} title={title}")
        return

    if args.hwnd is None and not args.title:
        raise SystemExit("请提供 --title、--hwnd，或使用 --list 查看窗口。")

    hwnd = args.hwnd if args.hwnd is not None else find_window_by_title(args.title, args.exact)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"开始截图: hwnd={hwnd}, interval={args.interval:g}s, output={output_dir.resolve()}")
    print("按 Ctrl+C 停止。")

    while True:
        image = capture_window(hwnd)
        output_path = build_output_path(output_dir, args.prefix)
        image.save(output_path)
        print(output_path)
        sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止截图。")
    except Exception as error:
        print(f"\n发生错误：{error}")
        if len(sys.argv) == 1:
            input("按回车退出...")
        raise SystemExit(1)
