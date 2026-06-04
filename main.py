import json
import shlex
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtGui import QTextCursor

from automation import choose_action_intent
from game_utils import LaunchResult, launch_game
from window_screenshot import find_window_by_title, get_window_rect, parse_hwnd


class MapleStoryToolWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MapleStory 工具")
        self.resize(820, 620)
        self.setMinimumSize(720, 520)
        self.current_hwnd: Optional[int] = None
        self.detection_process: Optional[QProcess] = None
        self.stdout_buffer = ""

        self.build_ui()

    def build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        title = QLabel("基础启动")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        main_layout.addWidget(title)

        launch_group = QGroupBox("游戏启动")
        launch_layout = QGridLayout(launch_group)
        launch_layout.setColumnStretch(1, 1)

        self.game_path_input = QLineEdit()
        self.game_path_input.setPlaceholderText("选择游戏 exe、启动器、快捷方式或 URL")
        game_path_button = QPushButton("选择文件")
        game_path_button.clicked.connect(self.choose_game_path)

        launch_layout.addWidget(QLabel("游戏路径"), 0, 0)
        launch_layout.addWidget(self.game_path_input, 0, 1)
        launch_layout.addWidget(game_path_button, 0, 2)

        self.working_dir_input = QLineEdit()
        self.working_dir_input.setPlaceholderText("默认使用游戏文件所在目录")
        working_dir_button = QPushButton("选择目录")
        working_dir_button.clicked.connect(self.choose_working_dir)

        launch_layout.addWidget(QLabel("工作目录"), 1, 0)
        launch_layout.addWidget(self.working_dir_input, 1, 1)
        launch_layout.addWidget(working_dir_button, 1, 2)

        self.launch_args_input = QLineEdit()
        self.launch_args_input.setPlaceholderText("可选，例如 --windowed")
        launch_layout.addWidget(QLabel("启动参数"), 2, 0)
        launch_layout.addWidget(self.launch_args_input, 2, 1, 1, 2)

        self.use_startfile_checkbox = QCheckBox("使用 Windows 默认方式打开，适合 .lnk 或 .url")
        launch_layout.addWidget(self.use_startfile_checkbox, 3, 1, 1, 2)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        launch_button = QPushButton("启动游戏")
        launch_button.clicked.connect(self.launch_selected_game)
        button_layout.addWidget(launch_button)
        launch_layout.addLayout(button_layout, 4, 1, 1, 2)

        main_layout.addWidget(launch_group)

        window_group = QGroupBox("连接现有窗口")
        window_layout = QGridLayout(window_group)
        window_layout.setColumnStretch(1, 1)

        self.window_selector_input = QLineEdit()
        self.window_selector_input.setPlaceholderText("输入窗口名称或 hwnd，例如 微信 / 123456 / 0x1e0420")
        window_layout.addWidget(QLabel("窗口名称/hwnd"), 0, 0)
        window_layout.addWidget(self.window_selector_input, 0, 1)

        self.exact_match_checkbox = QCheckBox("窗口名称精确匹配")
        window_layout.addWidget(self.exact_match_checkbox, 1, 1)

        connect_button = QPushButton("连接窗口")
        connect_button.clicked.connect(self.connect_existing_window)
        window_layout.addWidget(connect_button, 0, 2)

        self.connected_window_label = QLabel("未连接窗口")
        self.connected_window_label.setStyleSheet("color: #666;")
        window_layout.addWidget(self.connected_window_label, 2, 1, 1, 2)

        main_layout.addWidget(window_group)

        detection_group = QGroupBox("实时检测")
        detection_layout = QGridLayout(detection_group)
        detection_layout.setColumnStretch(1, 1)

        self.detect_interval_input = QLineEdit("1")
        self.detect_conf_input = QLineEdit("0.25")
        detection_layout.addWidget(QLabel("检测间隔秒"), 0, 0)
        detection_layout.addWidget(self.detect_interval_input, 0, 1)
        detection_layout.addWidget(QLabel("置信度"), 1, 0)
        detection_layout.addWidget(self.detect_conf_input, 1, 1)

        detection_button_layout = QHBoxLayout()
        detection_button_layout.addStretch(1)
        self.run_detection_button = QPushButton("运行检测脚本")
        self.run_detection_button.clicked.connect(self.start_detection_script)
        self.stop_detection_button = QPushButton("停止检测")
        self.stop_detection_button.setEnabled(False)
        self.stop_detection_button.clicked.connect(self.stop_detection_script)
        detection_button_layout.addWidget(self.run_detection_button)
        detection_button_layout.addWidget(self.stop_detection_button)
        detection_layout.addLayout(detection_button_layout, 2, 1)

        main_layout.addWidget(detection_group)

        monitor_group = QGroupBox("检测结果")
        monitor_layout = QVBoxLayout(monitor_group)

        self.frame_summary_label = QLabel("尚未收到检测结果。")
        self.frame_summary_label.setWordWrap(True)
        monitor_layout.addWidget(self.frame_summary_label)

        self.action_intent_label = QLabel("动作意图：wait")
        self.action_intent_label.setStyleSheet("font-weight: 600; color: #0057b8;")
        monitor_layout.addWidget(self.action_intent_label)

        self.detection_table = QTableWidget(0, 6)
        self.detection_table.setHorizontalHeaderLabels(["class", "conf", "center", "bbox", "distance", "rank"])
        self.detection_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.detection_table.verticalHeader().setVisible(False)
        self.detection_table.setEditTriggers(QTableWidget.NoEditTriggers)
        monitor_layout.addWidget(self.detection_table)

        main_layout.addWidget(monitor_group, stretch=1)

        self.status_output = QTextEdit()
        self.status_output.setReadOnly(True)
        self.status_output.setText("请选择游戏路径。")
        self.status_output.setMinimumHeight(110)
        main_layout.addWidget(QLabel("状态"))
        main_layout.addWidget(self.status_output)

        hint = QLabel("说明：当前 GUI 先承载基础启动功能，后续可以继续接入实时检测、目标选择和状态机。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        main_layout.addWidget(hint)

    def choose_game_path(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择游戏或启动器",
            "",
            "可执行文件/快捷方式 (*.exe *.lnk *.url);;所有文件 (*.*)",
        )
        if not file_path:
            return

        self.game_path_input.setText(file_path)
        if not self.working_dir_input.text().strip():
            suffix = Path(file_path).suffix.lower()
            if suffix not in {".lnk", ".url"}:
                self.working_dir_input.setText(str(Path(file_path).parent))

    def choose_working_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if directory:
            self.working_dir_input.setText(directory)

    def launch_selected_game(self) -> None:
        game_path = self.game_path_input.text().strip().strip('"')
        working_dir = self.working_dir_input.text().strip().strip('"') or None
        launch_args = self.parse_launch_args(self.launch_args_input.text())

        if not game_path:
            QMessageBox.warning(self, "缺少路径", "请先选择或输入游戏路径。")
            return

        try:
            result = launch_game(
                game_path=game_path,
                launch_args=launch_args,
                working_dir=working_dir,
                use_startfile=self.use_startfile_checkbox.isChecked(),
            )
        except Exception as error:
            self.set_status(f"启动失败：{error}")
            QMessageBox.critical(self, "启动失败", str(error))
            return

        self.set_status(self.format_launch_result(result))

    def connect_existing_window(self) -> None:
        selector = self.window_selector_input.text().strip().strip('"')
        if not selector:
            QMessageBox.warning(self, "缺少窗口信息", "请输入窗口名称或 hwnd。")
            return

        try:
            try:
                hwnd = parse_hwnd(selector)
            except ValueError:
                hwnd = find_window_by_title(selector, self.exact_match_checkbox.isChecked())

            left, top, right, bottom = get_window_rect(hwnd)
        except Exception as error:
            self.current_hwnd = None
            self.connected_window_label.setText("未连接窗口")
            self.set_status(f"连接窗口失败：{error}")
            QMessageBox.critical(self, "连接窗口失败", str(error))
            return

        self.current_hwnd = hwnd
        self.connected_window_label.setText(
            f"已连接 hwnd={hwnd}, size={right - left}x{bottom - top}, pos=({left},{top})"
        )
        self.set_status(f"已连接窗口：hwnd={hwnd}\n窗口区域：left={left}, top={top}, right={right}, bottom={bottom}")

    def start_detection_script(self) -> None:
        if self.current_hwnd is None:
            QMessageBox.warning(self, "未连接窗口", "请先输入窗口名称或 hwnd，并点击连接窗口。")
            return

        if self.detection_process is not None and self.detection_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "检测运行中", "检测脚本已经在运行。")
            return

        interval = self.detect_interval_input.text().strip() or "1"
        conf = self.detect_conf_input.text().strip() or "0.25"
        script_path = Path(__file__).resolve().parent / "realtime_detect.py"
        arguments = [
            str(script_path),
            "--hwnd",
            str(self.current_hwnd),
            "--interval",
            interval,
            "--conf",
            conf,
        ]

        self.detection_process = QProcess(self)
        self.detection_process.setWorkingDirectory(str(Path(__file__).resolve().parent))
        self.detection_process.readyReadStandardOutput.connect(self.read_detection_stdout)
        self.detection_process.readyReadStandardError.connect(self.read_detection_stderr)
        self.detection_process.finished.connect(self.on_detection_finished)
        self.detection_process.start(sys.executable, arguments)

        if not self.detection_process.waitForStarted(3000):
            self.set_status("检测脚本启动失败。")
            self.detection_process = None
            return

        self.run_detection_button.setEnabled(False)
        self.stop_detection_button.setEnabled(True)
        self.append_status(f"已启动检测脚本：{sys.executable} {' '.join(arguments)}")

    def stop_detection_script(self) -> None:
        if self.detection_process is None or self.detection_process.state() == QProcess.NotRunning:
            return

        self.detection_process.terminate()
        if not self.detection_process.waitForFinished(3000):
            self.detection_process.kill()
        self.append_status("已请求停止检测脚本。")

    def read_detection_stdout(self) -> None:
        if self.detection_process is None:
            return
        text = bytes(self.detection_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not text:
            return

        self.stdout_buffer += text
        while "\n" in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split("\n", 1)
            self.handle_detection_stdout_line(line.strip())

    def read_detection_stderr(self) -> None:
        if self.detection_process is None:
            return
        text = bytes(self.detection_process.readAllStandardError()).decode("utf-8", errors="replace")
        if text.strip():
            self.append_status(text.rstrip())

    def handle_detection_stdout_line(self, line: str) -> None:
        if not line:
            return

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.append_status(line)
            return

        self.update_detection_view(payload)

    def update_detection_view(self, payload: dict) -> None:
        detections = payload.get("detections") or []
        player_center = payload.get("player_center") or ["?", "?"]
        window_size = payload.get("window_size") or ["?", "?"]
        timestamp = payload.get("timestamp", "-")

        self.frame_summary_label.setText(
            f"时间：{timestamp} | 数量：{len(detections)} | "
            f"人物估算中心：{self.format_pair(player_center)} | 窗口尺寸：{self.format_pair(window_size)}"
        )

        action_intent = choose_action_intent(payload)
        self.action_intent_label.setText(
            f"动作意图：{action_intent.action} | 原因：{action_intent.reason}"
        )

        self.detection_table.setRowCount(len(detections))
        for row, detection in enumerate(detections):
            bbox = detection.get("bbox") or []
            center = detection.get("center") or []
            values = [
                str(detection.get("class_name", "-")),
                f"{float(detection.get('confidence', 0)):.2f}",
                self.format_pair(center),
                self.format_bbox(bbox),
                f"{float(detection.get('distance_from_center', 0)):.1f}",
                str(row + 1),
            ]
            for column, value in enumerate(values):
                self.detection_table.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def format_pair(value: list) -> str:
        if len(value) < 2:
            return "-"
        return f"({float(value[0]):.1f}, {float(value[1]):.1f})"

    @staticmethod
    def format_bbox(value: list) -> str:
        if len(value) < 4:
            return "-"
        return ", ".join(f"{float(item):.1f}" for item in value[:4])

    def on_detection_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self.stdout_buffer.strip():
            self.handle_detection_stdout_line(self.stdout_buffer.strip())
            self.stdout_buffer = ""
        self.run_detection_button.setEnabled(True)
        self.stop_detection_button.setEnabled(False)
        self.append_status(f"检测脚本已退出，exit_code={exit_code}")
        self.detection_process = None

    def set_status(self, text: str) -> None:
        self.status_output.setText(text)

    def append_status(self, text: str) -> None:
        current = self.status_output.toPlainText().strip()
        self.status_output.setText(f"{current}\n{text}" if current else text)
        self.status_output.moveCursor(QTextCursor.End)

    @staticmethod
    def parse_launch_args(value: str) -> list[str]:
        value = value.strip()
        if not value:
            return []
        return shlex.split(value)

    @staticmethod
    def format_launch_result(result: LaunchResult) -> str:
        parts = [
            f"已启动：{result.game_path}",
            f"工作目录：{result.working_dir}",
            f"启动方式：{result.method}",
        ]
        if result.process_id is not None:
            parts.append(f"进程 PID：{result.process_id}")
        return "\n".join(parts)

    def closeEvent(self, event) -> None:
        self.stop_detection_script()
        event.accept()


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    window = MapleStoryToolWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
