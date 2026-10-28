from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .ollama import Ollama
from .tasks import FunctionThread, WorkerProcess


def default_settings():
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    from .media import ROOT
    model_folder = ROOT / "models" if (ROOT / "models").exists() else folder / "models"
    return {"ollama_url": "http://127.0.0.1:11434", "text_model": "qwen3.5:4b",
            "vision_model": "qwen3.5:4b", "whisper_model": "small", "language": "ru",
            "models_dir": str(model_folder), "threads": 4, "performance": "Сбалансированный", "visual": "выключен",
            "frame_interval": 30, "transcribe": True, "text_search": True,
            "export_template": "{project}_{time}_{title}",
            "criteria": "Забавные диалоги, неожиданные реакции, законченные истории, полезные объяснения. "
                        "Пропускать рекламу, паузы и повторы.", "dictionary": ""}


class AnalysisDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Параметры локального анализа")
        self.resize(560, 540)
        self.settings = default_settings() | settings
        layout = QVBoxLayout(self)
        info = QLabel("Один тяжёлый этап за раз · CPU · результаты сохраняются по блокам")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        layout.addLayout(form)
        self.fields = {}
        for key, label in [("ollama_url", "Адрес Ollama"), ("text_model", "Текстовая модель"),
                           ("vision_model", "Модель изображения"), ("dictionary", "Имена и термины"),
                           ("export_template", "Имя: {project}, {time}, {title}")]:
            field = QLineEdit(str(self.settings[key]))
            self.fields[key] = field
            form.addRow(label, field)
        for key, label, choices in [("whisper_model", "Whisper", ["small", "medium"]),
                                    ("language", "Язык", ["ru", "авто"]),
                                    ("visual", "Изображение", ["выключен", "экономный", "подробный"])]:
            combo = QComboBox()
            combo.addItems(choices)
            combo.setCurrentText(self.settings[key] or "авто")
            self.fields[key] = combo
            form.addRow(label, combo)
        self.threads = QSpinBox()
        self.threads.setRange(1, 12)
        self.threads.setValue(self.settings["threads"])
        form.addRow("Потоки CPU (тихий: 2, баланс: 4)", self.threads)
        self.performance = QComboBox()
        self.performance.addItems(["Тихий", "Сбалансированный", "Максимальная скорость"])
        self.performance.setCurrentText(self.settings["performance"])
        self.performance.currentIndexChanged.connect(lambda i: self.threads.setValue([2, 4, 8][i]))
        form.addRow("Режим нагрузки", self.performance)
        self.interval = QComboBox()
        self.interval.addItems(["3", "5", "10", "30", "60"])
        self.interval.setCurrentText(str(self.settings["frame_interval"]))
        form.addRow("Интервал кадров, с", self.interval)
        self.asr = QCheckBox("Распознавать речь (Whisper должен быть скачан)")
        self.asr.setChecked(self.settings["transcribe"])
        self.text = QCheckBox("Искать моменты по расшифровке")
        self.text.setChecked(self.settings["text_search"])
        layout.addWidget(self.asr)
        layout.addWidget(self.text)
        layout.addWidget(QLabel("Что искать"))
        self.criteria = QPlainTextEdit(self.settings["criteria"])
        self.criteria.setMaximumHeight(100)
        layout.addWidget(self.criteria)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        result = dict(self.settings)
        for key, field in self.fields.items():
            result[key] = field.currentText() if isinstance(field, QComboBox) else field.text().strip()
        result["language"] = None if result["language"] == "авто" else "ru"
        result.update(threads=self.threads.value(), frame_interval=int(self.interval.currentText()),
                      performance=self.performance.currentText(),
                      transcribe=self.asr.isChecked(), text_search=self.text.isChecked(),
                      criteria=self.criteria.toPlainText())
        return result


class ModelsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Локальные модели")
        self.resize(610, 500)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url = QLineEdit(settings["ollama_url"])
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(["qwen3.5:4b", "qwen3.5:2b"])
        self.folder = QLineEdit(settings["models_dir"])
        form.addRow("Ollama", self.url)
        form.addRow("Модель", self.model)
        form.addRow("Кэш Whisper", self.folder)
        layout.addLayout(form)
        choose = QPushButton("Выбрать папку Whisper…")
        choose.clicked.connect(self.choose_folder)
        layout.addWidget(choose)
        row = QHBoxLayout()
        for title, action in [("Проверить Ollama", self.refresh), ("Размер и возможности", self.inspect),
                              ("Скачать Ollama", self.pull)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for model in ("small", "medium"):
            button = QPushButton(f"Скачать Whisper {model}")
            button.clicked.connect(lambda checked=False, m=model: self.whisper(m))
            row.addWidget(button)
        free = QPushButton("Выгрузить выбранную модель")
        free.clicked.connect(self.release)
        row.addWidget(free)
        layout.addLayout(row)
        tests = QHBoxLayout()
        for label, mode in [("Тест текста", "test-text"), ("Тест речи · 30 с", "test-asr"), ("Описать кадр", "test-image")]:
            test = QPushButton(label)
            test.clicked.connect(lambda checked=False, m=mode: self.test_model(m))
            tests.addWidget(test)
        layout.addLayout(tests)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlainText("Модели пользователя сохраняются.\nQwen3.5 4B: около 3,4 ГБ; 2B: около 2,7 ГБ.\n"
                                 "Уточните размер кнопкой перед скачиванием. Whisper small: около 0,5 ГБ.\n"
                                 "Первоначальное скачивание требует интернета; дальнейшая работа — локально.")
        layout.addWidget(self.output)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        stop = QPushButton("Остановить загрузку")
        stop.clicked.connect(lambda: self.worker.stop())
        layout.addWidget(stop)
        self.worker = WorkerProcess(self)
        self.worker.event.connect(self.on_event)
        self.checked_model = None
        self.lookup = None

    def submit(self, function, callback):
        if self.lookup and self.lookup.isRunning():
            self.output.appendPlainText("Дождитесь текущей проверки")
            return
        self.lookup = FunctionThread(function, self)
        self.lookup.result.connect(callback)
        self.lookup.error.connect(lambda text: self.output.appendPlainText("Ошибка: "+text))
        self.lookup.finished.connect(self.lookup_finished)
        self.lookup.start()

    def lookup_finished(self):
        self.lookup.deleteLater()
        self.lookup = None

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка моделей Whisper", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def refresh(self):
        url = self.url.text()
        def display(models):
            self.output.appendPlainText("Ollama доступна\n" + "\n".join(
                f"{m['name']} · {m['size']/1e9:.2f} ГБ" for m in models))
            current = self.model.currentText()
            self.model.clear()
            self.model.addItems(list(dict.fromkeys([current, "qwen3.5:4b", "qwen3.5:2b"]+[m["name"] for m in models])))
        self.submit(lambda: Ollama(url).models(), display)

    def inspect(self):
        model = self.model.currentText()
        def fetch():
            import httpx
            name, _, tag = model.partition(":")
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                r = client.get(f"https://registry.ollama.ai/v2/library/{name}/manifests/{tag or 'latest'}")
                r.raise_for_status()
                return sum(layer["size"] for layer in r.json()["layers"])
        def display(size):
            self.checked_model = model
            self.output.appendPlainText(f"{model}: загрузка {size/1e9:.2f} ГБ. "
                                       + ("Qwen3.5 поддерживает текст и изображения.\n" if model.startswith("qwen3.5:") else "\n") +
                                       "Совместимость окончательно проверяет установленная Ollama при загрузке.")
        self.submit(fetch, display)

    def pull(self):
        if self.worker.running():
            return
        if self.checked_model != self.model.currentText():
            self.inspect()
            self.output.appendPlainText("После проверки размера нажмите «Скачать Ollama» ещё раз")
            return
        self.progress.setRange(0, 100)
        self.worker.start(["pull", self.model.currentText(), "--url", self.url.text()])

    def whisper(self, model):
        if self.worker.running():
            return
        Path(self.folder.text()).mkdir(parents=True, exist_ok=True)
        self.progress.setRange(0, 0)
        self.worker.start(["whisper", model, "--folder", self.folder.text()])

    def release(self):
        url, model = self.url.text(), self.model.currentText()
        self.submit(lambda: Ollama(url).release(model), lambda _: self.output.appendPlainText("Выбранная модель выгружена из памяти"))

    def test_model(self, mode):
        if self.worker.running():
            return
        if mode == "test-text":
            args = [mode, self.model.currentText(), "--url", self.url.text()]
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Выберите изображение" if mode == "test-image" else "Выберите аудио или видео")
            if not path:
                return
            args = [mode, path, "--model", "small" if mode == "test-asr" else self.model.currentText(),
                    "--url", self.url.text(), "--folder", self.folder.text()]
        self.output.appendPlainText("Локальный тест выполняется…")
        self.progress.setRange(0, 0)
        self.worker.start(args)

    def on_event(self, event):
        self.output.appendPlainText(event.get("text", ""))
        if "progress" in event:
            self.progress.setValue(int(event["progress"]*100))
        if event["kind"] in ("done", "error"):
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if event["kind"] == "done" else 0)

    def closeEvent(self, event):
        if self.lookup and self.lookup.isRunning():
            self.output.appendPlainText("Завершается проверка соединения; окно можно будет закрыть через несколько секунд")
            event.ignore()
            return
        if self.worker.running():
            self.worker.stop()
        self.settings.update(ollama_url=self.url.text(), models_dir=self.folder.text())
        event.accept()
