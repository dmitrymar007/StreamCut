"""First-run preparation, with one action and recoverable downloads."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QTemporaryDir, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .tasks import WorkerProcess


class SetupDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("StreamCut · подготовка нейросетей")
        self.resize(650, 540)
        self.success = False
        self.temporary = QTemporaryDir()
        layout = QVBoxLayout(self)
        title = QLabel("Подготовим всё для локального анализа")
        title.setObjectName("heading")
        layout.addWidget(title)
        explanation = QLabel("Установим Ollama, если её ещё нет, и скачаем недостающие модели. "
            "Для NVIDIA автоматически подготовим CUDA-библиотеки. Python и CUDA Toolkit устанавливать не нужно.\n\n"
            "Обычно загружается 4–7 ГБ; оставьте 20 ГБ свободного места. Нужен интернет. "
            "После подготовки анализ работает локально. Уже установленные модели сохраняются. "
            "Для NVIDIA нужен актуальный драйвер; без совместимой видеокарты используется CPU.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.device = QComboBox()
        self.device.addItem("Авто — NVIDIA CUDA, иначе CPU", "auto")
        self.device.addItem("Только CPU — без загрузки CUDA", "cpu")
        self.device.addItem("Обязательно NVIDIA CUDA", "cuda")
        self.device.setCurrentIndex(max(0, self.device.findData(settings.get("whisper_device", "auto"))))
        layout.addWidget(self.device)
        self.model = QComboBox()
        self.model.addItem("Qwen3.5 4B — основной вариант", "qwen3.5:4b")
        self.model.addItem("Qwen3.5 2B — меньше памяти и загрузка", "qwen3.5:2b")
        self.model.setCurrentIndex(max(0, self.model.findData(settings.get("text_model", "qwen3.5:4b"))))
        layout.addWidget(self.model)
        licenses = QLabel('Компоненты: <a href="https://github.com/ollama/ollama/blob/main/LICENSE">Ollama</a>, '
            '<a href="https://huggingface.co/Systran/faster-whisper-small">Whisper</a>, '
            '<a href="https://ollama.com/library/qwen3.5">Qwen</a>, '
            '<a href="https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html">NVIDIA</a>. '
            'Нажимая «Подготовить», вы соглашаетесь с лицензиями загружаемых компонентов.')
        licenses.setWordWrap(True)
        licenses.setOpenExternalLinks(True)
        layout.addWidget(licenses)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlainText("Редактор и ручной экспорт доступны также без нейросетей. Подготовку можно выполнить позже через меню «Модели».")
        layout.addWidget(self.output, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.activity = QLabel("Ожидание запуска")
        self.activity.setWordWrap(True)
        layout.addWidget(self.activity)
        self.last_update = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_activity)
        row = QHBoxLayout()
        self.start = QPushButton("Подготовить нейросети")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.begin)
        row.addWidget(self.start)
        self.stop = QPushButton("Остановить")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        row.addWidget(self.stop)
        self.finish = QPushButton("Позже — открыть редактор")
        self.finish.clicked.connect(self.accept)
        row.addWidget(self.finish)
        layout.addLayout(row)
        self.worker = WorkerProcess(self)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.finished_task)

    def begin(self):
        if self.worker.running():
            return
        values = self.settings | {"whisper_device": self.device.currentData(), "text_model": self.model.currentData(),
                                  "vision_model": self.model.currentData()}
        snapshot = Path(self.temporary.path())/"setup.json"
        snapshot.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        self.start.setEnabled(False)
        self.device.setEnabled(False)
        self.model.setEnabled(False)
        self.stop.setEnabled(True)
        self.finish.setEnabled(False)
        self.progress.setValue(0)
        self.last_update = time.monotonic()
        self.activity.setText("Запуск подготовки…")
        self.worker.start(["prepare", str(snapshot)])
        if self.worker.log_path:
            self.output.appendPlainText("Журнал подготовки: "+str(self.worker.log_path))
        self.timer.start()

    def update_activity(self):
        elapsed = int(time.monotonic()-self.last_update)
        if elapsed >= 5:
            self.activity.setText(f"Ожидание следующего обновления: {elapsed} с. "
                                 "При сетевой ошибке загрузка повторится автоматически; можно остановить и продолжить позже.")

    def on_event(self, event):
        self.last_update = time.monotonic()
        self.activity.setText("Подготовка выполняется…")
        text = event.get("text")
        if text:
            # Keep the log bounded even on many gigabytes of layer progress.
            self.output.appendPlainText(text)
            if self.output.document().blockCount() > 250:
                cursor = self.output.textCursor()
                cursor.movePosition(cursor.MoveOperation.Start)
                cursor.select(cursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()
        if "progress" in event:
            self.progress.setValue(round(event["progress"]*100))
        if event["kind"] == "done":
            self.success = True
            self.settings.update(whisper_device=self.device.currentData(), text_model=self.model.currentData(),
                                 vision_model=self.model.currentData())
            self.progress.setValue(100)
            self.finish.setText("Готово — открыть редактор")
        elif event["kind"] == "error":
            self.output.appendPlainText("Исправьте причину и повторите. Завершённые загрузки сохраняются.")

    def finished_task(self):
        self.timer.stop()
        self.activity.setText("Подготовка завершена" if self.success else "Подготовка не завершена; доступен повтор")
        self.stop.setEnabled(False)
        self.finish.setEnabled(True)
        self.device.setEnabled(True)
        self.model.setEnabled(True)
        self.start.setEnabled(not self.success)
        if not self.success:
            self.start.setText("Повторить подготовку")

    def cancel(self):
        self.worker.stop()
        self.output.appendPlainText("Подготовка остановлена. Можно продолжить позже; загрузки с проверкой SHA256 сохраняются.")

    def done(self, result):
        if self.worker.running():
            self.cancel()
        super().done(result)
