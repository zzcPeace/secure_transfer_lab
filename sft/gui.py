"""Qt stays on the main thread; one QThread owns the asyncio networking loop."""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .client import TransferClient, TransferPaused
from .config import ClientConfig
from .diagnostics import ensure_gui_environment, hide_x11_hostname
from .tasks import TaskJournal

STATUS = {
    "queued": "排队中",
    "hashing": "计算校验值",
    "transferring": "传输中",
    "verifying": "完整性校验",
    "reconnecting": "正在重连",
    "paused": "已暂停",
    "completed": "已完成",
    "error": "失败，可重试",
    "cancelled": "已取消",
}


def human_size(value):
    number = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if number < 1024 or unit == "GiB":
            return f"{number:.1f} {unit}"
        number /= 1024


class NetworkWorker(QThread):
    ready = Signal()
    connected = Signal(dict)
    disconnected = Signal()
    files_changed = Signal(list)
    task_changed = Signal(dict)
    error = Signal(str)
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = None
        self.client = None
        self.journal = None
        self.jobs = {}
        self.pauses = {}
        self.connecting = False

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self.ready.emit)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def submit(self, operation, *args):
        if not self.loop or not self.loop.is_running():
            self.error.emit("网络线程尚未就绪")
            return
        future = asyncio.run_coroutine_threadsafe(getattr(self, operation)(*args), self.loop)

        def done(result):
            try:
                result.result()
            except Exception as exc:
                self.error.emit(str(exc))

        future.add_done_callback(done)

    async def connect_profile(self, path):
        if self.connecting:
            raise ValueError("正在连接，请稍候")
        self.connecting = True
        try:
            await self._connect_profile(path)
        finally:
            self.connecting = False

    async def _connect_profile(self, path):
        await self.disconnect_client()
        config = ClientConfig.load(path)
        journal = TaskJournal(config.state_dir)
        client = TransferClient(config)
        try:
            identity = await client.connect()
        except BaseException:
            await client.close()
            raise
        self.client, self.journal = client, journal
        self.connected.emit(identity)
        for record in journal.records.values():
            self.task_changed.emit(dict(record))
        await self.refresh()

    async def disconnect_client(self):
        for pause in self.pauses.values():
            pause.set()
        jobs = list(self.jobs.values())
        # Cancellation also interrupts stalled network requests and closes that channel.
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for task_id in self.jobs:
            record = self.journal.records[task_id]
            record.update(status="paused", speed=0, message="已保存进度，可继续")
            self.task_changed.emit(dict(record))
        self.jobs.clear()
        self.pauses.clear()
        if self.journal:
            self.journal.save()
        if self.client:
            await self.client.close()
            self.client = None
        self.disconnected.emit()

    async def shutdown(self):
        await self.disconnect_client()
        self.loop.call_soon(self.loop.stop)

    async def refresh(self):
        if self.client:
            self.files_changed.emit(await self.client.list_files())

    async def add_uploads(self, paths):
        if not self.client:
            raise ValueError("请先连接服务器")
        for path in paths:
            record = self.journal.create("upload", path)
            self._schedule(record)

    async def add_downloads(self, files, directory):
        if not self.client:
            raise ValueError("请先连接服务器")
        for info in files:
            record = self.journal.create(
                "download", Path(directory) / info["name"], file_id=info["id"]
            )
            record["total"] = info["size"]
            self._schedule(record)

    def _schedule(self, record):
        if record["id"] in self.jobs:
            return
        record.update(status="queued", message="", speed=0)
        pause = asyncio.Event()
        self.pauses[record["id"]] = pause
        self.task_changed.emit(dict(record))
        self.jobs[record["id"]] = asyncio.create_task(self._run_task(record, pause))

    async def _run_task(self, record, pause):
        last_time, last_done, last_save = time.monotonic(), record.get("done", 0), 0.0

        def progress(event):
            nonlocal last_time, last_done, last_save
            now = time.monotonic()
            old_status = record["status"]
            record.update(event)
            done = record.get("done", 0)
            if now - last_time >= 0.25:
                record["speed"] = max(0, done - last_done) / (now - last_time)
                last_time, last_done = now, done
            if record["status"] in ("completed", "paused", "error"):
                record["speed"] = 0
            self.task_changed.emit(dict(record))
            if now - last_save > 2 or old_status != record["status"]:
                self.journal.save()
                last_save = now

        try:
            if record["direction"] == "upload":
                result = await self.client.upload(
                    record["local_path"], name=record["name"], progress=progress, pause=pause
                )
            else:
                result = await self.client.download(
                    record["file_id"], record["local_path"], progress=progress, pause=pause
                )
            record["file_id"] = result["id"]
            record.update(status="completed", speed=0, message="SHA-256 校验通过")
            self.log_message.emit(f"完成：{record['name']}，SHA-256 校验通过")
        except (TransferPaused, asyncio.CancelledError):
            record.update(status="paused", speed=0, message="已保存进度，可继续")
        except Exception as exc:
            record.update(status="error", speed=0, message=str(exc))
            self.log_message.emit(f"任务失败：{record['name']} — {exc}")
        finally:
            self.jobs.pop(record["id"], None)
            self.pauses.pop(record["id"], None)
            self.journal.save()
            self.task_changed.emit(dict(record))
        if self.client and record["status"] == "completed":
            try:
                await self.refresh()
            except Exception as exc:
                self.log_message.emit(f"刷新文件列表失败：{exc}")

    async def pause_tasks(self, ids):
        for task_id in ids:
            if task_id in self.pauses:
                self.pauses[task_id].set()
                if self.journal.records[task_id]["status"] == "queued":
                    await self._cancel_job(task_id)
                    record = self.journal.records[task_id]
                    record.update(status="paused", speed=0, message="已暂停排队，可继续")
                    self.journal.save()
                    self.task_changed.emit(dict(record))

    async def _cancel_job(self, task_id):
        job = self.jobs.get(task_id)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        # A task cancelled before its coroutine starts never executes its finally block.
        self.jobs.pop(task_id, None)
        self.pauses.pop(task_id, None)

    async def resume_tasks(self, ids):
        if not self.client:
            raise ValueError("请先连接服务器")
        for task_id in ids:
            record = self.journal.records[task_id]
            if record["status"] in ("paused", "error"):
                self._schedule(record)

    async def cancel_tasks(self, ids):
        for task_id in ids:
            record = self.journal.records[task_id]
            if record["status"] in ("completed", "cancelled"):
                continue
            if task_id in self.jobs:
                await self._cancel_job(task_id)
            if record["direction"] == "upload" and record.get("transfer_id"):
                await self.client.abort_upload(record["transfer_id"])
            record.update(
                status="cancelled",
                speed=0,
                message="上传配额已释放；下载片段保留在目标目录，可重新添加下载",
            )
            self.journal.save()
            self.task_changed.emit(dict(record))

    async def delete_files(self, file_ids):
        for file_id in file_ids:
            await self.client.delete_file(file_id)
        await self.refresh()


class MainWindow(QMainWindow):
    def __init__(self, profile="runtime/clients/alice/client.json"):
        super().__init__()
        self.setWindowTitle("Secure Transfer · 软件与系统安全实验 1B")
        self.resize(1180, 820)
        self.rows = {}
        self.remote_files = []
        self.online = False
        self.closing = False
        self.worker = NetworkWorker(self)
        self._build(profile)
        hide_x11_hostname(self)
        self.worker.ready.connect(self._worker_ready)
        self.worker.connected.connect(self._connected)
        self.worker.disconnected.connect(self._disconnected)
        self.worker.files_changed.connect(self._files)
        self.worker.task_changed.connect(self._task)
        self.worker.error.connect(self._error)
        self.worker.log_message.connect(self.log.appendPlainText)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _build(self, profile):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 18)
        title = QLabel("Secure Transfer")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("双向 TLS 认证 · 多文件并发 · 断点续传 · 用户带宽控制"))
        connection = QHBoxLayout()
        self.profile = QLineEdit(str(Path(profile).absolute()))
        browse = QPushButton("选择配置…")
        browse.clicked.connect(self._browse_profile)
        self.connect_button = QPushButton("连接服务器")
        self.connect_button.setEnabled(False)
        self.connect_button.clicked.connect(self._connect_click)
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.clicked.connect(lambda: self.worker.submit("disconnect_client"))
        connection.addWidget(QLabel("客户端配置"))
        connection.addWidget(self.profile, 1)
        connection.addWidget(browse)
        connection.addWidget(self.connect_button)
        connection.addWidget(self.disconnect_button)
        layout.addLayout(connection)
        self.identity = QLabel("未连接。选择对应用户的 client.json，点击连接服务器。")
        self.identity.setObjectName("identity")
        layout.addWidget(self.identity)
        toolbar = QHBoxLayout()
        self.upload_button = QPushButton("＋ 上传文件…")
        self.upload_button.clicked.connect(self._upload)
        self.download_button = QPushButton("↓ 下载选中文件…")
        self.download_button.clicked.connect(self._download)
        self.refresh_button = QPushButton("刷新列表")
        self.refresh_button.clicked.connect(lambda: self.worker.submit("refresh"))
        self.delete_button = QPushButton("删除远端文件")
        self.delete_button.clicked.connect(self._delete)
        for button in (
            self.upload_button,
            self.download_button,
            self.refresh_button,
            self.delete_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Vertical)
        files_panel = QWidget()
        files_layout = QVBoxLayout(files_panel)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.addWidget(QLabel("我的远端文件（不同用户互相隔离）"))
        self.file_table = self._table(["文件名", "大小", "SHA-256", "文件 ID"])
        files_layout.addWidget(self.file_table)
        splitter.addWidget(files_panel)
        tasks_panel = QWidget()
        tasks_layout = QVBoxLayout(tasks_panel)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        task_bar = QHBoxLayout()
        task_bar.addWidget(QLabel("传输任务 · 最多 3 个并发任务"))
        task_bar.addStretch()
        self.pause_button = QPushButton("暂停选中")
        self.pause_button.clicked.connect(
            lambda: self.worker.submit("pause_tasks", self._selected_tasks())
        )
        self.resume_button = QPushButton("继续 / 重试")
        self.resume_button.clicked.connect(
            lambda: self.worker.submit("resume_tasks", self._selected_tasks())
        )
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.clicked.connect(self._cancel)
        for button in (self.pause_button, self.resume_button, self.cancel_button):
            task_bar.addWidget(button)
        tasks_layout.addLayout(task_bar)
        self.task_table = self._table(
            ["方向", "文件名", "进度", "已完成 / 总量", "速度", "状态", "详情"]
        )
        tasks_layout.addWidget(self.task_table)
        splitter.addWidget(tasks_panel)
        splitter.setSizes([250, 310])
        layout.addWidget(splitter, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMaximumHeight(100)
        self.log.setPlaceholderText("连接状态、传输完成情况与错误信息…")
        layout.addWidget(self.log)
        self.statusBar().showMessage("就绪 · TCP keepalive + 应用层心跳 · 传输完成后连接继续复用")
        self.setStyleSheet("""
            QMainWindow { background: #f5f7fa; }
            QLabel#title { font-size: 27px; font-weight: 700; color: #143354; }
            QLabel#identity { background: #e8eff7; padding: 10px; border-radius: 5px; }
            QPushButton { padding: 7px 12px; }
            QLineEdit { padding: 7px; }
            QTableWidget { background: white; gridline-color: #e4e9ef; }
            QHeaderView::section { background: #e9eef4; padding: 6px; border: 0; }
            QProgressBar { border: 1px solid #d5dde8; border-radius: 3px; text-align: center; }
            QProgressBar::chunk { background: #3b86c8; }
        """)
        self._set_online(False)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _set_online(self, online):
        self.online = online
        for button in (
            self.disconnect_button,
            self.upload_button,
            self.download_button,
            self.refresh_button,
            self.delete_button,
            self.pause_button,
            self.resume_button,
            self.cancel_button,
        ):
            button.setEnabled(online)
        self.profile.setEnabled(not online)

    def _browse_profile(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择客户端配置", str(Path(self.profile.text()).parent), "JSON (*.json)"
        )
        if path:
            self.profile.setText(path)

    def _connect_click(self):
        self.connect_button.setEnabled(False)
        self.identity.setText("正在验证证书并建立连接…")
        self.worker.submit("connect_profile", self.profile.text())

    def _connected(self, identity):
        self.rows.clear()
        self.task_table.setRowCount(0)
        self._set_online(True)
        self.connect_button.setEnabled(True)
        self.identity.setText(
            f"已连接 · {identity['user']} / {identity['role'].upper()} · TLS 1.3 双向认证 · "
            f"上传 {human_size(identity['upload_bps'])}/s · 下载 {human_size(identity['download_bps'])}/s"
        )
        self.log.appendPlainText("证书验证通过，控制连接及传输连接已建立。")

    def _disconnected(self):
        self._set_online(False)
        self.connect_button.setEnabled(True)
        self.file_table.setRowCount(0)
        self.remote_files = []
        self.rows.clear()
        self.task_table.setRowCount(0)
        self.identity.setText("已断开；未完成任务的进度已保存。")

    def _files(self, files):
        if not self.online:
            return
        self.remote_files = files
        self.file_table.setRowCount(len(files))
        for row, info in enumerate(files):
            for column, text in enumerate(
                (info["name"], human_size(info["size"]), info["sha256"][:20] + "…", info["id"])
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(json.dumps(info, ensure_ascii=False, indent=2))
                self.file_table.setItem(row, column, item)

    def _task(self, record):
        if not self.online:
            return
        task_id = record["id"]
        if task_id not in self.rows:
            row = self.task_table.rowCount()
            self.task_table.insertRow(row)
            self.rows[task_id] = row
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.task_table.setCellWidget(row, 2, bar)
        row = self.rows[task_id]
        values = {
            0: "上传" if record["direction"] == "upload" else "下载",
            1: record["name"],
            3: f"{human_size(record['done'])} / {human_size(record['total'])}",
            4: human_size(record.get("speed", 0)) + "/s",
            5: STATUS.get(record["status"], record["status"]),
            6: record.get("message", ""),
        }
        for col, value in values.items():
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, task_id)
            item.setToolTip(value)
            self.task_table.setItem(row, col, item)
        percent = int(100 * record["done"] / record["total"]) if record["total"] else 0
        self.task_table.cellWidget(row, 2).setValue(
            100 if record["status"] == "completed" else min(100, percent)
        )

    def _selected_tasks(self):
        return [
            self.task_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.task_table.selectionModel().selectedRows()
        ]

    def _selected_files(self):
        return [
            self.remote_files[index.row()]
            for index in self.file_table.selectionModel().selectedRows()
        ]

    def _upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件")
        if paths:
            self.worker.submit("add_uploads", paths)

    def _download(self):
        files = self._selected_files()
        if not files:
            self.statusBar().showMessage("请先在远端文件表中选择文件。")
            return
        directory = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if directory:
            self.worker.submit("add_downloads", files, directory)

    def _delete(self):
        files = self._selected_files()
        if (
            files
            and QMessageBox.question(
                self, "删除远端文件", f"确定删除选中的 {len(files)} 个远端文件？"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.worker.submit("delete_files", [f["id"] for f in files])

    def _cancel(self):
        ids = self._selected_tasks()
        if (
            ids
            and QMessageBox.question(
                self, "取消任务", "取消选中任务？未完成上传将删除并释放配额；本地下载片段保留。"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.worker.submit("cancel_tasks", ids)

    def _error(self, message):
        self.connect_button.setEnabled(True)
        self.log.appendPlainText("错误：" + message)
        self.statusBar().showMessage(message)

    def _worker_finished(self):
        if self.closing:
            self.close()

    def _worker_ready(self):
        self.connect_button.setEnabled(True)
        if self.closing:
            self.worker.submit("shutdown")

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.centralWidget().setEnabled(False)
                if self.worker.loop and self.worker.loop.is_running():
                    self.worker.submit("shutdown")
        else:
            event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="runtime/clients/alice/client.json")
    args = parser.parse_args(argv)
    try:
        ensure_gui_environment()
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = MainWindow(args.profile)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
