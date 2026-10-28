from __future__ import annotations

import json
import os
import re
from pathlib import Path

from PySide6.QtCore import QSettings, QStringListModel, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .captions import read_captions
from .dialogs import AnalysisDialog, ModelsDialog, default_settings
from .domain import (
    Clip,
    ExportJob,
    ExportOptions,
    Project,
    Segment,
    Span,
    fingerprint,
    merge_candidate,
    parse_time,
    timecode,
    uid,
)
from .media import VENDOR, probe
from .storage import ProjectStore
from .tasks import ExportThread, PreviewThread, WorkerProcess
from .widgets import ClipDelegate, ClipListModel, Timeline


def button(text, callback, primary=False, tip=""):
    result = QPushButton(text)
    result.clicked.connect(callback)
    if primary:
        result.setObjectName("primary")
    result.setToolTip(tip or text)
    return result


def panel(title):
    widget = QWidget()
    widget.setObjectName("panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(12, 12, 12, 12)
    label = QLabel(title)
    label.setObjectName("heading")
    layout.addWidget(label)
    return widget, layout


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StreamCut · локальная мастерская клипов")
        self.resize(1360, 900)
        self.setMinimumSize(1050, 710)
        self.setAcceptDrops(True)
        self.preferences = QSettings()
        self.project = Project(settings=default_settings())
        try:
            self.project.settings.update(json.loads(self.preferences.value("analysis_settings", "{}")))
        except (ValueError, TypeError):
            pass
        self.store = None
        self.selected = None
        self.player = None
        self.player_error = ""
        self.export_thread = None
        self.preview_thread = None
        self.position = 0.0
        self.undo_stack, self.redo_stack = [], []
        self.loading = False
        self.source_valid = False
        self.worker = WorkerProcess(self)
        self.worker.event.connect(self.analysis_event)
        self.worker.finished.connect(self.analysis_finished)
        self.create_ui()
        self.create_menu()
        self.create_shortcuts()
        self.create_player()
        geometry = self.preferences.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        sizes = self.preferences.value("panels")
        if sizes:
            self.splitter.restoreState(sizes)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.timeout.connect(self.save)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)
        self.overview.viewChanged.connect(lambda: self.preview_timer.start(600))
        self.timeline.viewChanged.connect(lambda: self.preview_timer.start(600))
        self.statusBar().showMessage("Откройте видео — ручная нарезка доступна без моделей")
        self.refresh_clips()

    def create_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(9)
        top = QHBoxLayout()
        brand = QLabel("◩  StreamCut")
        brand.setObjectName("brand")
        top.addWidget(brand)
        self.source_label = QLabel("Мастерская клипов  /  Новый проект")
        self.source_label.setObjectName("muted")
        top.addWidget(self.source_label, 1)
        top.addWidget(button("Открыть видео", self.choose_source))
        top.addWidget(button("Проекты", self.choose_project))
        top.addWidget(button("Модели", self.models))
        top.addWidget(button("Настройки", self.configure))
        layout.addLayout(top)
        progress_row = QHBoxLayout()
        self.analysis_label = QLabel("Локальный анализ · готов к настройке")
        self.analysis_label.setObjectName("muted")
        progress_row.addWidget(self.analysis_label, 1)
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedWidth(150)
        self.analysis_progress.setRange(0, 100)
        self.analysis_progress.setValue(0)
        progress_row.addWidget(self.analysis_progress)
        self.analyze_button = button("Начать анализ", self.start_analysis, True)
        progress_row.addWidget(self.analyze_button)
        self.pause_button = button("Пауза", self.pause_analysis)
        self.pause_button.setEnabled(False)
        progress_row.addWidget(self.pause_button)
        stop = button("Остановить", self.stop_analysis)
        progress_row.addWidget(stop)
        layout.addLayout(progress_row)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)
        left, ll = panel("Моменты")
        left.setMinimumWidth(220)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, речи, заметке…")
        self.search.textChanged.connect(self.refresh_clips)
        ll.addWidget(self.search)
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["все статусы", "новый", "просмотрен", "оставлен", "отклонён", "в очереди", "экспортирован", "ошибка экспорта"])
        self.filter.currentTextChanged.connect(self.refresh_clips)
        self.sort = QComboBox()
        self.sort.addItems(["по времени", "по оценке", "по длительности"])
        self.sort.currentTextChanged.connect(self.refresh_clips)
        row.addWidget(self.filter)
        row.addWidget(self.sort)
        ll.addLayout(row)
        row = QHBoxLayout()
        self.origin = QComboBox()
        self.origin.addItems(["все источники", "вручную", "речь", "изображение"])
        self.origin.currentTextChanged.connect(self.refresh_clips)
        row.addWidget(self.origin)
        self.hide_done = QCheckBox("Скрыть готовые")
        self.hide_done.toggled.connect(self.refresh_clips)
        row.addWidget(self.hide_done)
        ll.addLayout(row)
        self.clip_model = ClipListModel()
        self.clip_list = QListView()
        self.clip_list.setModel(self.clip_model)
        self.clip_list.setItemDelegate(ClipDelegate())
        self.clip_list.setUniformItemSizes(True)
        self.clip_list.selectionModel().currentChanged.connect(self.select_index)
        ll.addWidget(self.clip_list, 1)
        self.empty = QLabel("Пока нет моментов.\nДобавьте вручную или запустите анализ.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(button("+ Момент", self.add_clip))
        row.addWidget(button("Следующий", self.next_clip, tip="Следующий непроверенный момент"))
        ll.addLayout(row)
        self.splitter.addWidget(left)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.video = QFrame()
        self.video.setStyleSheet("background: #080b11; border-radius: 9px;")
        self.video.setMinimumSize(320, 200)
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.winId()
        video_layout = QVBoxLayout(self.video)
        self.welcome = QLabel("Откройте запись стрима\n\nПеретащите видео сюда или нажмите «Открыть видео»\nВесь исходник доступен для просмотра и нарезки")
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome.setWordWrap(True)
        self.welcome.setStyleSheet("color: #9babc5; font-size: 14px; background: transparent;")
        video_layout.addWidget(self.welcome)
        cl.addWidget(self.video, 4)
        controls = QHBoxLayout()
        controls.addWidget(button("▶ / Ⅱ", self.toggle_play, tip="Воспроизведение / пауза · Space"))
        controls.addWidget(button("‹ кадр", lambda: self.frame_step(-1)))
        controls.addWidget(button("кадр ›", lambda: self.frame_step(1)))
        self.clock = QLineEdit("00:00:00.000")
        self.clock.setMaximumWidth(115)
        self.clock.returnPressed.connect(lambda: self.safe_seek(self.clock.text()))
        controls.addWidget(self.clock)
        self.total = QLabel("/ 00:00:00")
        controls.addWidget(self.total)
        self.speed = QComboBox()
        self.speed.addItems(["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"])
        self.speed.setCurrentText("1×")
        self.speed.currentTextChanged.connect(self.set_speed)
        controls.addWidget(self.speed)
        self.loop = QCheckBox("Цикл")
        controls.addWidget(self.loop)
        controls.addStretch()
        cl.addLayout(controls)
        self.audio = QComboBox()
        self.audio.setToolTip("Аудиодорожка для просмотра, распознавания и экспорта")
        self.audio.currentIndexChanged.connect(self.change_audio)
        cl.addWidget(self.audio)
        self.transcript_panel, tl = panel("Расшифровка · тайминги приблизительные")
        self.transcript_search = QLineEdit()
        self.transcript_search.setPlaceholderText("Найти реплику…")
        self.transcript_search.textChanged.connect(self.refresh_transcript)
        tl.addWidget(self.transcript_search)
        self.transcript_model = QStringListModel()
        self.transcript = QListView()
        self.transcript.setModel(self.transcript_model)
        self.transcript.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.transcript.clicked.connect(self.seek_segment)
        self.transcript.doubleClicked.connect(self.edit_segment)
        tl.addWidget(self.transcript)
        transcript_buttons = QHBoxLayout()
        transcript_buttons.addWidget(button("Импорт SRT / VTT", self.import_captions))
        label = QLabel("Щелчок — переход · двойной — правка")
        label.setObjectName("muted")
        transcript_buttons.addWidget(label)
        tl.addLayout(transcript_buttons)
        self.transcript_panel.setMinimumHeight(120)
        self.transcript_panel.setMaximumHeight(240)
        cl.addWidget(self.transcript_panel, 2)
        self.splitter.addWidget(center)

        self.inspector, il = panel("Параметры клипа")
        self.inspector.setMinimumWidth(235)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Название момента")
        self.title.editingFinished.connect(self.edit_metadata)
        il.addWidget(self.title)
        form = QFormLayout()
        self.inpoint = QLineEdit("00:00:00.000")
        self.outpoint = QLineEdit("00:00:30.000")
        self.inpoint.editingFinished.connect(self.edit_bounds)
        self.outpoint.editingFinished.connect(self.edit_bounds)
        form.addRow("Начало · I", self.inpoint)
        form.addRow("Конец · O", self.outpoint)
        il.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("I — начало", lambda: self.mark(True)))
        row.addWidget(button("O — конец", lambda: self.mark(False)))
        il.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("Проверить начало", lambda: self.seek(self.current().start) if self.current() else None))
        row.addWidget(button("Конец", lambda: self.seek(max(self.current().start, self.current().end-3)) if self.current() else None))
        il.addLayout(row)
        self.status = QComboBox()
        self.status.addItems(["новый", "просмотрен", "оставлен", "отклонён"])
        self.status.currentTextChanged.connect(self.edit_metadata)
        il.addWidget(self.status)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Заметка редактора")
        self.note.setMaximumHeight(64)
        self.note.textChanged.connect(self.edit_metadata)
        il.addWidget(self.note)
        self.options = ExportOptions()
        export_form = QFormLayout()
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["исходный", "9:16", "16:9", "1:1", "размытый фон"])
        export_form.addRow("Кадр", self.layout_choice)
        self.export_mode = QComboBox()
        self.export_mode.addItems(["точный", "быстрый"])
        self.export_mode.setToolTip("Быстрое копирование зависит от ключевых кадров; границы приблизительные. Без фильтров звука.")
        export_form.addRow("Экспорт", self.export_mode)
        self.resolution = QComboBox()
        self.resolution.addItems(["1080", "720", "480"])
        export_form.addRow("Высота, px", self.resolution)
        self.crf = QSpinBox()
        self.crf.setRange(15, 35)
        self.crf.setValue(20)
        self.crf.setToolTip("Меньше — выше качество и больше файл")
        export_form.addRow("CRF", self.crf)
        self.crop = QSlider(Qt.Orientation.Horizontal)
        self.crop.setRange(0, 100)
        self.crop.setValue(50)
        export_form.addRow("Кадр по горизонтали", self.crop)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0, 3)
        self.volume.setSingleStep(0.1)
        self.volume.setValue(1)
        export_form.addRow("Громкость ×", self.volume)
        il.addLayout(export_form)
        self.burn = QCheckBox("Прожечь субтитры (+ отдельный SRT)")
        il.addWidget(self.burn)
        il.addWidget(button("Предпросмотр результата…", self.export_preview))
        self.reason = QLabel("Выберите момент, чтобы увидеть обоснование и источники.")
        self.reason.setWordWrap(True)
        self.reason.setObjectName("muted")
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        il.addWidget(self.reason)
        il.addStretch()
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setWidget(self.inspector)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setMinimumWidth(255)
        self.inspector.setMinimumHeight(760)
        self.splitter.addWidget(self.inspector_scroll)
        self.splitter.setSizes([280, 760, 270])
        self.splitter.setStretchFactor(1, 1)

        timeline_header = QHBoxLayout()
        label = QLabel("ВЕСЬ ИСХОДНИК   ·   синие границы — клип   ·   бирюзовые отметки — проверенные кадры")
        label.setObjectName("muted")
        timeline_header.addWidget(label, 1)
        timeline_header.addWidget(button("−", lambda: self.zoom(1.5)))
        timeline_header.addWidget(button("+", lambda: self.zoom(0.67)))
        layout.addLayout(timeline_header)
        self.overview, self.timeline = Timeline(True), Timeline()
        for timeline in (self.overview, self.timeline):
            timeline.seek.connect(self.seek)
            timeline.boundaries.connect(self.apply_bounds)
            layout.addWidget(timeline)
        bottom = QHBoxLayout()
        self.queue_label = QLabel("Очередь экспорта пуста")
        self.queue_label.setObjectName("muted")
        bottom.addWidget(self.queue_label, 1)
        bottom.addWidget(button("Очередь", self.show_queue))
        bottom.addWidget(button("Папка экспорта", self.choose_export_folder))
        bottom.addWidget(button("Экспортировать", self.enqueue))
        bottom.addWidget(button("Экспортировать и далее  ↵", self.export_next, True, "Ctrl+Enter"))
        layout.addLayout(bottom)

    def create_menu(self):
        menu = self.menuBar().addMenu("Проект")
        for text, action in [("Открыть видео…", self.choose_source), ("Открыть проект…", self.choose_project),
                             ("Переподключить исходник…", self.relink), ("Сохранить", self.save),
                             ("Импорт SRT/VTT…", self.import_captions), ("Экспорт всех оставленных", self.export_kept)]:
            item = menu.addAction(text)
            item.triggered.connect(action)
        view = self.menuBar().addMenu("Вид")
        for text, widget in [("Расшифровка", self.transcript_panel), ("Инспектор клипа", self.inspector_scroll)]:
            action = QAction(text, self, checkable=True, checked=True)
            action.toggled.connect(widget.setVisible)
            view.addAction(action)
        edit = self.menuBar().addMenu("Правка")
        edit.addAction("Отменить", self.undo)
        edit.addAction("Повторить", self.redo)
        edit.addAction("Горячие клавиши…", self.configure_shortcuts)
        edit.addAction("Повторный визуальный анализ выделения", self.analyze_selection)
        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction("О программе", lambda: QMessageBox.information(self, "StreamCut",
            "StreamCut 0.1 · Windows / CPU\nI/O — границы · Space — пауза · ←/→ — 5 секунд\n"
            "Ctrl+Enter — экспорт и далее · Ctrl+Z / Ctrl+Shift+Z — отмена/повтор\n"
            "Результаты моделей требуют проверки. Быстрый экспорт зависит от ключевых кадров.\n"
            "Покадровый шаг назад зависит от формата. Визуальный анализ проверяет выбранные кадры, не каждую секунду."))

    def create_shortcuts(self):
        self.shortcuts = []
        actions = [("play", "Space", self.toggle_play), ("in", "I", lambda: self.mark(True)),
                   ("out", "O", lambda: self.mark(False)), ("back", "Left", lambda: self.seek(self.position-5)),
                   ("forward", "Right", lambda: self.seek(self.position+5)),
                   ("export", "Ctrl+Return", self.export_next), ("undo", "Ctrl+Z", self.undo),
                   ("redo", "Ctrl+Shift+Z", self.redo)]
        for key, default, action in actions:
            sequence = self.preferences.value("key/"+key, default)
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(lambda fn=action: fn() if not isinstance(
                QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)) else None)
            self.shortcuts.append(shortcut)

    def configure_shortcuts(self):
        key, ok = QInputDialog.getItem(self, "Горячие клавиши", "Действие", ["play", "in", "out", "back", "forward", "export", "undo", "redo"], editable=False)
        if ok:
            value, accepted = QInputDialog.getText(self, "Сочетание", "Например: Ctrl+I", text=str(self.preferences.value("key/"+key, "")))
            if accepted and value:
                self.preferences.setValue("key/"+key, value)
                for shortcut in self.shortcuts:
                    shortcut.setEnabled(False)
                    shortcut.deleteLater()
                self.create_shortcuts()

    def create_player(self):
        try:
            os.environ["PATH"] = str(VENDOR)+os.pathsep+os.environ["PATH"]
            if os.name == "nt":
                self.dll_directory = os.add_dll_directory(str(VENDOR))
            import mpv
            self.player = mpv.MPV(wid=str(int(self.video.winId())), hwdec="auto-safe", keep_open=True,
                                  input_default_bindings=False, input_vo_keyboard=False, osc=False,
                                  idle=True, pause=True, volume=100)
        except Exception as exc:
            self.player_error = str(exc)
            self.welcome.setText("Не удалось загрузить libmpv.\nЗапустите scripts/setup_media.py\n"+str(exc))

    def error(self, text):
        self.statusBar().showMessage(str(text), 15000)
        QMessageBox.warning(self, "StreamCut", str(text))

    def current(self):
        return next((c for c in self.project.clips if c.id == self.selected), None)

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть запись", "", "Видео (*.mp4 *.mkv *.mov *.webm *.flv *.ts);;Все файлы (*)")
        if path:
            self.open_source(path)

    def can_switch(self):
        if self.worker.running() or (self.export_thread and self.export_thread.isRunning()) or (self.preview_thread and self.preview_thread.isRunning()):
            self.error("Дождитесь текущей фоновой операции или остановите её перед сменой проекта")
            return False
        return True

    def open_source(self, path):
        if not self.can_switch():
            return
        try:
            data = probe(path)
            self.save()
            settings = dict(self.project.settings)
            self.project = Project(title=Path(path).stem, source=str(Path(path).resolve()), **data, settings=settings)
            folder = Path(settings["models_dir"]).parent / "projects" / (Path(path).stem[:70]+"-"+uid()[:8])
            self.store = ProjectStore(folder)
            self.selected = None
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.source_valid = True
            self.load_media()
            self.save()
        except Exception as exc:
            self.error(exc)

    def choose_project(self):
        recent = self.preferences.value("recent_project", "")
        folder = QFileDialog.getExistingDirectory(self, "Папка проекта (с project.sqlite)", str(recent))
        if folder:
            self.open_project(folder)

    def open_project(self, folder):
        if not self.can_switch():
            return
        try:
            store = ProjectStore(folder)
            project = store.load()
            self.save()
            self.project, self.store = project, store
            self.selected = None
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.source_valid = Path(project.source).exists() and fingerprint(project.source) == project.fingerprint
            self.load_media()
            if not self.source_valid:
                self.welcome.setText("Исходник перемещён или изменён.\nПроект → Переподключить исходник")
                self.welcome.show()
                self.statusBar().showMessage("Исходник недоступен. Правки сохранены; экспорт заблокирован до переподключения.")
            self.update_queue()
        except Exception as exc:
            self.error(exc)

    def relink(self):
        if not self.store or not self.can_switch():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Найти исходную запись")
        if path:
            try:
                if fingerprint(path) != self.project.fingerprint:
                    raise ValueError("Этот файл отличается от исходника. Откройте его как новый проект, чтобы не применять старые тайминги и кэш")
                self.project.source = path
                for job in self.project.jobs:
                    if job.state in ("ожидает", "ошибка экспорта", "отменён"):
                        job.source = path
                self.source_valid = True
                self.load_media()
                self.save()
            except Exception as exc:
                self.error(exc)

    def load_media(self):
        if self.player:
            self.player.command("stop")
            if self.source_valid:
                self.player.play(self.project.source)
                self.player.pause = True
        self.welcome.setVisible(not self.source_valid or not self.player)
        self.source_label.setText(self.project.title[:70])
        self.source_label.setToolTip(self.project.source + "\nПроект: " + str(self.store.folder))
        self.total.setText("/ "+timecode(self.project.duration)[:-4])
        self.audio.blockSignals(True)
        self.audio.clear()
        for i, track in enumerate(self.project.audio_tracks):
            self.audio.addItem(f"Звук {i+1} · {track['language']} {track['label']}")
        self.audio.setCurrentIndex(self.project.audio)
        self.audio.blockSignals(False)
        for timeline in (self.overview, self.timeline):
            timeline.duration = max(1, self.project.duration)
            timeline.clips = self.project.clips
            timeline.coverage = self.project.coverage
            timeline.peaks = []
            timeline.thumbnails = []
            timeline.update()
        self.refresh_clips()
        self.refresh_transcript()
        if self.project.clips:
            self.select_clip(self.project.clips[0])
        self.preview_timer.start(1000)

    def change_audio(self, index):
        if self.loading or index < 0:
            return
        if self.worker.running():
            self.stop_analysis()
        if index != self.project.audio and self.project.segments:
            self.project.settings["transcript_archive"] = [s.model_dump() for s in self.project.segments]
            self.project.segments.clear()
            self.refresh_transcript()
        self.project.audio = index
        if self.player and self.source_valid:
            self.player.aid = index+1
        self.save()
        self.preview_timer.start(400)

    def toggle_play(self):
        if self.player and self.source_valid:
            self.player.pause = not self.player.pause

    def seek(self, position):
        if self.player and self.source_valid:
            position = max(0, min(self.project.duration-0.001, position))
            self.player.command("seek", position, "absolute+exact")
            self.position = position
            self.preview_timer.start(650)

    def safe_seek(self, text):
        try:
            self.seek(parse_time(text))
        except ValueError as exc:
            self.error(exc)

    def frame_step(self, direction):
        if self.player and self.source_valid:
            self.player.pause = True
            self.player.command("frame-step" if direction > 0 else "frame-back-step")

    def set_speed(self, value):
        if self.player:
            self.player.speed = float(value.replace("×", ""))

    def tick(self):
        if self.player and self.source_valid:
            try:
                self.position = float(self.player.time_pos or 0)
                current = self.current()
                if self.loop.isChecked() and current and self.position >= current.end:
                    self.seek(current.start)
            except Exception:
                pass
        if not self.clock.hasFocus():
            self.clock.setText(timecode(self.position))
        for timeline in (self.overview, self.timeline):
            timeline.position = self.position
            timeline.update()

    def refresh_clips(self, *_):
        query = self.search.text().casefold()
        by_id = {s.id: s.text for s in self.project.segments} if query else {}
        items = []
        for clip in self.project.clips:
            if self.filter.currentIndex() and clip.status != self.filter.currentText():
                continue
            if self.origin.currentIndex() and self.origin.currentText() not in clip.sources:
                continue
            if self.hide_done.isChecked() and clip.status in ("экспортирован", "отклонён"):
                continue
            content = " ".join([clip.title, clip.reason, clip.note]+[by_id.get(i, "") for i in clip.evidence])
            if query and query not in content.casefold():
                continue
            items.append(clip)
        sort = self.sort.currentIndex()
        items.sort(key=lambda c: c.start if sort == 0 else -(c.score or 0) if sort == 1 else -c.duration)
        self.clip_list.selectionModel().blockSignals(True)
        self.clip_model.replace(items)
        for i, clip in enumerate(items):
            if clip.id == self.selected:
                self.clip_list.setCurrentIndex(self.clip_model.index(i))
        self.clip_list.selectionModel().blockSignals(False)
        self.empty.setVisible(not items)
        self.overview.clips = self.project.clips
        self.timeline.clips = self.project.clips

    def select_index(self, index, previous):
        if index.isValid():
            self.select_clip(self.clip_model.items[index.row()])

    def select_clip(self, clip):
        self.selected = clip.id
        if clip.status == "новый":
            clip.status = "просмотрен"
        self.loading = True
        self.title.setText(clip.title)
        self.note.setPlainText(clip.note)
        self.status.setCurrentText(clip.status if clip.status in [self.status.itemText(i) for i in range(self.status.count())] else "оставлен")
        self.reason.setText("ОБОСНОВАНИЕ\n"+clip.reason+"\n\nИсточники: "+", ".join(clip.sources)+
                            (f"\nОценка модели: {clip.score:.0f}/100 (не вероятность)" if clip.score is not None else ""))
        self.inpoint.setText(timecode(clip.start))
        self.outpoint.setText(timecode(clip.end))
        self.loading = False
        for timeline in (self.overview, self.timeline):
            timeline.start, timeline.end = clip.start, clip.end
        self.seek(clip.start)
        self.changed()

    def add_clip(self):
        if not self.source_valid:
            return self.error("Сначала откройте видео")
        start = min(self.position, max(0, self.project.duration-0.05))
        clip = Clip(title=f"Момент {len(self.project.clips)+1:02}", spans=[Span(start=start, end=min(self.project.duration, start+30))])
        self.project.clips.append(clip)
        self.select_clip(clip)
        self.refresh_clips()

    def history(self):
        clip = self.current()
        if clip:
            self.undo_stack.append(clip.model_dump())
            self.redo_stack.clear()

    def apply_bounds(self, start, end):
        clip = self.current()
        if not clip:
            return
        try:
            span = Span(start=start, end=end)
            if span.end > self.project.duration:
                raise ValueError("Граница выходит за конец исходника")
            if clip.start != start or clip.end != end:
                self.history()
                clip.spans = [span]
                clip.edited = True
            self.inpoint.setText(timecode(start))
            self.outpoint.setText(timecode(end))
            for timeline in (self.overview, self.timeline):
                timeline.start, timeline.end = start, end
            self.changed()
            self.refresh_clips()
        except ValueError as exc:
            self.error(exc)

    def edit_bounds(self):
        if not self.loading:
            try:
                self.apply_bounds(parse_time(self.inpoint.text()), parse_time(self.outpoint.text()))
            except ValueError as exc:
                self.error(exc)

    def mark(self, start):
        clip = self.current()
        if not clip:
            self.add_clip()
            clip = self.current()
        if clip:
            self.apply_bounds(self.position if start else clip.start, clip.end if start else self.position)

    def edit_metadata(self, *_):
        clip = self.current()
        if clip and not self.loading:
            clip.title = self.title.text() or "Без названия"
            clip.note = self.note.toPlainText()
            clip.status = self.status.currentText()
            clip.edited = True
            self.changed()
            self.refresh_clips()

    def undo(self):
        self.restore_edit(self.undo_stack, self.redo_stack)

    def redo(self):
        self.restore_edit(self.redo_stack, self.undo_stack)

    def restore_edit(self, source, destination):
        if source:
            snapshot = Clip.model_validate(source.pop())
            for i, current in enumerate(self.project.clips):
                if current.id == snapshot.id:
                    destination.append(current.model_dump())
                    self.project.clips[i] = snapshot
                    self.select_clip(snapshot)
                    self.refresh_clips()
                    break

    def next_clip(self):
        items = sorted(self.project.clips, key=lambda c: c.start)
        current = self.current()
        following = [c for c in items if c.status == "новый" and (not current or c.start > current.start)]
        if not following:
            following = [c for c in items if c.status == "новый"]
        if following:
            self.select_clip(following[0])
            self.refresh_clips()
        else:
            self.statusBar().showMessage("Непроверенных моментов больше нет", 5000)

    def changed(self):
        if not self.loading:
            self.autosave.start(400)

    def save(self):
        if self.store:
            try:
                self.store.save(self.project)
                self.preferences.setValue("recent_project", str(self.store.folder))
                self.statusBar().showMessage("Сохранено · "+str(self.store.folder), 3000)
            except Exception as exc:
                self.analysis_label.setText("Ошибка сохранения: "+str(exc))

    def refresh_transcript(self, *_):
        query = self.transcript_search.text().casefold()
        self.visible_segments = [s for s in sorted(self.project.segments, key=lambda s: s.start) if query in s.text.casefold()]
        self.transcript_model.setStringList([f"{timecode(s.start)[:-4]}   {s.text}" for s in self.visible_segments])

    def seek_segment(self, index):
        if index.isValid():
            self.seek(self.visible_segments[index.row()].start)

    def edit_segment(self, index):
        segment = self.visible_segments[index.row()]
        text, ok = QInputDialog.getMultiLineText(self, "Исправить реплику", "Текст", segment.text)
        if ok:
            segment.text = text
            self.refresh_transcript()
            self.changed()

    def import_captions(self):
        if not self.store:
            return self.error("Сначала откройте видео")
        if self.worker.running():
            return self.error("Остановите анализ перед импортом расшифровки")
        path, _ = QFileDialog.getOpenFileName(self, "Импорт расшифровки", "", "Субтитры (*.srt *.vtt)")
        if path:
            try:
                segments = read_captions(path)
                if any(s.end > self.project.duration+1 for s in segments):
                    raise ValueError("Расшифровка выходит за длительность исходника")
                self.project.segments = segments
                self.project.settings["transcribe"] = False
                self.refresh_transcript()
                self.save()
            except Exception as exc:
                self.error(exc)

    def configure(self):
        dialog = AnalysisDialog(self.project.settings, self)
        if dialog.exec():
            self.project.settings = dialog.values()
            self.project.settings.pop("visual_start", None)
            self.project.settings.pop("visual_end", None)
            self.preferences.setValue("analysis_settings", json.dumps(self.project.settings, ensure_ascii=False))
            self.save()

    def models(self):
        if self.worker.running():
            return self.error("Остановите анализ перед загрузкой или выгрузкой моделей")
        dialog = ModelsDialog(self.project.settings, self)
        dialog.exec()
        self.preferences.setValue("analysis_settings", json.dumps(self.project.settings, ensure_ascii=False))
        self.save()

    def start_analysis(self):
        if not self.source_valid or not self.store:
            return self.error("Откройте доступный исходник")
        if self.worker.running():
            return
        self.save()
        folder = self.store.folder / "cache"
        (folder / "pause").unlink(missing_ok=True)
        snapshot = folder / "analysis.json"
        snapshot.write_text(self.project.model_dump_json(), encoding="utf-8")
        self.worker.start(["analyze", str(snapshot), "--folder", str(folder)])
        self.analyze_button.setEnabled(False)
        self.pause_button.setEnabled(True)

    def analyze_selection(self):
        clip = self.current()
        if clip:
            self.project.settings.update(visual_start=clip.start, visual_end=clip.end, transcribe=False,
                                         text_search=False, visual="подробный")
            self.start_analysis()

    def pause_analysis(self):
        if not self.store or not self.worker.running():
            return
        path = self.store.folder / "cache" / "pause"
        if path.exists():
            path.unlink()
            self.pause_button.setText("Пауза")
        else:
            path.touch()
            self.pause_button.setText("Продолжить")
            self.analysis_label.setText("Пауза после текущего блока · сохранение результатов")

    def stop_analysis(self):
        if self.worker.running():
            self.worker.stop()
            self.save()
            self.analysis_label.setText("Остановлено · завершённые блоки сохранены · можно продолжить")

    def analysis_event(self, event):
        kind = event["kind"]
        if "text" in event:
            self.analysis_label.setText(event["text"])
            self.analysis_label.setToolTip(event["text"])
        if "progress" in event:
            self.analysis_progress.setValue(int(event["progress"]*100))
        if kind == "segments":
            ids = {s.id for s in self.project.segments}
            self.project.segments.extend(Segment.model_validate(s) for s in event["items"] if s["id"] not in ids)
            self.refresh_transcript()
        if kind in ("clips", "visual"):
            for data in event["items"]:
                merge_candidate(self.project.clips, Clip.model_validate(data))
            self.refresh_clips()
        if kind == "visual":
            self.project.coverage.extend(Span(start=t, end=min(self.project.duration, t+0.001)) for t in event["samples"])
            for timeline in (self.overview, self.timeline):
                timeline.coverage = self.project.coverage
        if "checkpoint" in event:
            self.project.checkpoints.setdefault("analysis", []).append(event["checkpoint"])
            self.save()

    def analysis_finished(self):
        self.analyze_button.setEnabled(True)
        self.analyze_button.setText("Продолжить анализ")
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Пауза")
        self.save()

    def choose_export_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка готовых клипов", self.project.settings.get("export_dir", ""))
        if folder:
            self.project.settings["export_dir"] = folder
            self.save()

    def export_options(self):
        return ExportOptions(mode=self.export_mode.currentText(), layout=self.layout_choice.currentText(),
                             crf=self.crf.value(), height=int(self.resolution.currentText()),
                             crop_x=self.crop.value()/100, volume=self.volume.value(), subtitles=self.burn.isChecked())

    def enqueue(self, checked=False, clip=None, destination=None):
        clip = clip or self.current()
        if not clip or not self.source_valid:
            self.error("Выберите момент с доступным исходником")
            return False
        self.edit_bounds()
        folder = self.project.settings.get("export_dir")
        if not folder and not destination:
            self.choose_export_folder()
            folder = self.project.settings.get("export_dir")
            if not folder:
                return False
        if len(clip.spans) != 1:
            self.error("Составной экспорт ещё не подключён")
            return False
        safe = lambda s: re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")[:70]
        name = safe(self.project.title)+"_"+timecode(clip.start).replace(":", "-")+"_"+safe(clip.title)
        path = Path(destination) if destination else Path(folder)/(name+"_"+uid()[:6]+".mp4")
        job = ExportJob(clip=clip.model_copy(deep=True), source=self.project.source,
                        fingerprint=self.project.fingerprint, audio=self.project.audio,
                        options=self.export_options(), segments=[s.model_copy(deep=True) for s in self.project.segments
                            if s.end > clip.start and s.start < clip.end], destination=str(path))
        self.project.jobs.append(job)
        clip.status = "в очереди"
        self.save()
        self.refresh_clips()
        self.update_queue()
        self.run_queue()
        return True

    def export_next(self):
        if self.enqueue():
            self.next_clip()

    def export_kept(self):
        for clip in list(self.project.clips):
            if clip.status == "оставлен" and not self.enqueue(clip=clip):
                break

    def run_queue(self):
        if self.export_thread and self.export_thread.isRunning():
            return
        if not self.source_valid or self.project.settings.get("queue_paused", False):
            return
        job = next((j for j in self.project.jobs if j.state == "ожидает"), None)
        if job:
            job.state = "выполняется"
            self.save()
            self.export_thread = ExportThread(job)
            self.export_thread.progress.connect(self.export_progress)
            self.export_thread.result.connect(self.export_result)
            self.export_thread.finished.connect(self.queue_finished)
            self.export_thread.start()

    def queue_finished(self):
        self.export_thread.deleteLater()
        self.export_thread = None
        self.run_queue()

    def export_progress(self, identity, value):
        job = next(j for j in self.project.jobs if j.id == identity)
        job.progress = value
        self.update_queue()

    def export_result(self, identity, state, error):
        job = next(j for j in self.project.jobs if j.id == identity)
        job.state, job.error = state, error
        clip = next((c for c in self.project.clips if c.id == job.clip.id), None)
        if clip:
            clip.status = state if state != "отменён" else "оставлен"
        self.save()
        self.refresh_clips()
        self.update_queue()
        if job.destination.endswith("preview.mp4") and state == "экспортирован":
            self.show_rendered_preview(job.destination)

    def show_rendered_preview(self, path):
        import mpv
        dialog = QDialog(self)
        dialog.setWindowTitle("Предпросмотр · точный результат экспорта")
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        video = QFrame()
        video.setMinimumHeight(400)
        video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        layout.addWidget(video, 1)
        player = mpv.MPV(wid=str(int(video.winId())), hwdec="auto-safe", keep_open=True, osc=True)
        layout.addWidget(button("▶ / Ⅱ", lambda: setattr(player, "pause", not player.pause)))
        dialog.finished.connect(lambda _: player.terminate())
        player.play(path)
        dialog.exec()

    def update_queue(self):
        jobs = self.project.jobs
        waiting = sum(j.state in ("ожидает", "выполняется") for j in jobs)
        done = sum(j.state == "экспортирован" for j in jobs)
        errors = sum(j.state == "ошибка экспорта" for j in jobs)
        active = next((j for j in jobs if j.state == "выполняется"), None)
        self.queue_label.setText(f"Очередь: {waiting} · готово: {done} · ошибок: {errors}"+
                                (f" · {active.progress:.0%}" if active else ""))

    def show_queue(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Очередь и история экспорта")
        dialog.resize(760, 410)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        layout.addWidget(listing)
        def refresh():
            index = listing.currentRow()
            listing.clear()
            for job in self.project.jobs:
                listing.addItem(f"{job.state} {job.progress:.0%} · {job.clip.title}\n{job.destination}\n{job.error}")
            listing.setCurrentRow(index)
        refresh()
        def action(mode):
            index = listing.currentRow()
            if not 0 <= index < len(self.project.jobs):
                return
            job = self.project.jobs[index]
            if mode == "retry" and job.state in ("ошибка экспорта", "отменён"):
                job.state, job.error, job.progress = "ожидает", "", 0
                self.run_queue()
            elif mode == "cancel":
                if self.export_thread and self.export_thread.job.id == job.id:
                    self.export_thread.cancel.set()
                elif job.state == "ожидает":
                    job.state = "отменён"
            elif mode in ("file", "folder"):
                path = Path(job.destination) if mode == "file" else Path(job.destination).parent
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            self.save()
            refresh()
        row = QHBoxLayout()
        for label, mode in [("Отменить", "cancel"), ("Повторить", "retry"), ("Открыть файл", "file"), ("Открыть папку", "folder")]:
            row.addWidget(button(label, lambda checked=False, m=mode: action(m)))
        pause = QCheckBox("Пауза очереди после текущего экспорта")
        pause.setChecked(self.project.settings.get("queue_paused", False))
        def toggle_queue(checked):
            self.project.settings["queue_paused"] = checked
            self.save()
            self.run_queue()
        pause.toggled.connect(toggle_queue)
        layout.addWidget(pause)
        layout.addLayout(row)
        timer = QTimer(dialog)
        timer.timeout.connect(refresh)
        timer.start(1000)
        dialog.exec()

    def export_preview(self):
        if self.store and self.current():
            destination = self.store.folder / "cache" / (uid()[:8]+"preview.mp4")
            self.enqueue(destination=str(destination))
            self.statusBar().showMessage("Предпросмотр рендерится с настройками экспорта; файл откроется после готовности")

    def zoom(self, factor):
        self.timeline.window = max(5, min(self.project.duration, self.timeline.window*factor))
        self.timeline.update()
        self.preview_timer.start(500)

    def update_preview(self):
        if not self.store or not self.source_valid or (self.preview_thread and self.preview_thread.isRunning()):
            return
        start, end = self.timeline.limits()
        self.preview_thread = PreviewThread(self.project.source, self.project.audio if self.project.audio_tracks else -1,
                                            start, end, self.store.folder/"cache")
        self.preview_thread.ready.connect(self.preview_ready)
        self.preview_thread.finished.connect(self.preview_finished)
        self.preview_thread.start()

    def preview_finished(self):
        if self.preview_thread:
            self.preview_thread.deleteLater()
            self.preview_thread = None

    def preview_ready(self, peaks, frames):
        self.timeline.peaks = peaks
        images = []
        for stamp, data in frames:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            images.append((stamp, pixmap))
        self.timeline.thumbnails = images
        self.timeline.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.open_source(urls[0].toLocalFile())

    def closeEvent(self, event):
        self.worker.stop()
        if self.export_thread and self.export_thread.isRunning():
            self.export_thread.cancel.set()
            self.export_thread.wait(6000)
            if self.export_thread.isRunning():
                event.ignore()
                self.statusBar().showMessage("Завершается остановка FFmpeg… повторите закрытие через несколько секунд")
                return
        if self.preview_thread and self.preview_thread.isRunning():
            self.preview_thread.wait(1000)
            if self.preview_thread.isRunning():
                event.ignore()
                self.statusBar().showMessage("Завершается подготовка таймлайна… повторите закрытие через несколько секунд")
                return
        self.save()
        self.preferences.setValue("geometry", self.saveGeometry())
        self.preferences.setValue("panels", self.splitter.saveState())
        if self.player:
            self.player.terminate()
        event.accept()
