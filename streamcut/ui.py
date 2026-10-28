from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QSettings, QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
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
    candidate_was_deleted,
    fingerprint,
    merge_candidate,
    output_dimensions,
    parse_time,
    remove_range,
    timecode,
    uid,
)
from .editing import apply_edit
from .media import ROOT, VENDOR, probe, video_filter
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
    playbackError = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("StreamCut 0.2 · редактор видео")
        self.setWindowIcon(QIcon(str(ROOT/"assets"/"streamcut.ico")))
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
        self.project_lock = None
        self.selected = None
        self.player = None
        self.player_error = ""
        self.export_thread = None
        self.preview_thread = None
        self.position = 0.0
        self.pending_seek = None
        self._seek_exact = True
        self._seek_target = None
        self._seek_started = 0.0
        self.scrubbing = False
        self._scrub_latest = None
        self._preview_generation = 0
        self._preview_request = None
        self._last_preview_layout = None
        self.undo_stack, self.redo_stack = [], []
        self.loading = False
        self.source_valid = False
        self.worker = WorkerProcess(self)
        self.worker.event.connect(self.analysis_event)
        self.worker.finished.connect(self.analysis_finished)
        self.playbackError.connect(lambda text: self.statusBar().showMessage("Ошибка плеера: "+text, 15000))
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
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(self.width(), available.width()-20), min(self.height(), available.height()-50))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.timeout.connect(self.save)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)
        self.timeline.viewChanged.connect(self.timeline_view_changed)
        self.scrub_timer = QTimer(self)
        self.scrub_timer.setInterval(65)
        self.scrub_timer.timeout.connect(self.flush_scrub)
        self.format_timer = QTimer(self)
        self.format_timer.setSingleShot(True)
        self.format_timer.timeout.connect(self.apply_output_preview)
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
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.source_label, 1)
        top.addWidget(button("Открыть видео", self.choose_source))
        top.addWidget(button("Проекты", self.choose_project))
        top.addWidget(button("Модели", self.models))
        top.addWidget(button("Настройки", self.configure))
        layout.addLayout(top)
        progress_row = QHBoxLayout()
        self.analysis_label = QLabel("Локальный анализ · готов к настройке")
        self.analysis_label.setObjectName("muted")
        self.analysis_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        self.clip_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clip_list.customContextMenuRequested.connect(self.clip_menu)
        self.clip_list.selectionModel().currentChanged.connect(self.select_index)
        ll.addWidget(self.clip_list, 1)
        self.empty = QLabel("Пока нет моментов.\nДобавьте вручную или запустите анализ.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(button("+ Момент", self.add_clip))
        row.addWidget(button("Удалить", self.delete_clip, tip="Удалить момент · Delete. Отмена: Ctrl+Z"))
        row.addWidget(button("Следующий", self.next_clip, tip="Следующий непроверенный момент"))
        ll.addLayout(row)
        self.splitter.addWidget(left)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.video = QFrame()
        self.video.setStyleSheet("background: #080b11; border-radius: 9px;")
        self.video.setMinimumSize(320, 140)
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
        self.transcript.setMinimumHeight(50)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.clicked.connect(self.seek_segment)
        self.transcript.doubleClicked.connect(self.edit_segment)
        tl.addWidget(self.transcript)
        transcript_buttons = QHBoxLayout()
        transcript_buttons.addWidget(button("Импорт SRT / VTT", self.import_captions))
        label = QLabel("Щелчок — переход · двойной — правка")
        label.setObjectName("muted")
        transcript_buttons.addWidget(label)
        tl.addLayout(transcript_buttons)
        self.transcript_panel.setMinimumHeight(190)
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
        fragments = QHBoxLayout()
        fragments.addWidget(button("Фрагменты…", self.edit_fragments))
        fragments.addWidget(button("Вырезать внутри…", self.cut_inside))
        il.addLayout(fragments)
        self.status.addItems(["новый", "просмотрен", "оставлен", "отклонён"])
        self.status.currentTextChanged.connect(self.edit_metadata)
        il.addWidget(self.status)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Заметка редактора")
        self.note.setMaximumHeight(64)
        self.note.textChanged.connect(self.edit_metadata)
        il.addWidget(self.note)
        self.options = ExportOptions()
        formats = QHBoxLayout()
        self.original_button = button("Оригинал", lambda: self.set_output_layout("исходный"))
        self.shorts_button = button("Shorts 9:16", lambda: self.set_output_layout("9:16"))
        self.original_button.setCheckable(True)
        self.shorts_button.setCheckable(True)
        formats.addWidget(self.original_button)
        formats.addWidget(self.shorts_button)
        il.insertLayout(1, formats)
        export_form = QFormLayout()
        self.export_form = export_form
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["исходный", "9:16", "16:9", "1:1", "размытый фон", "вебкамера + игра"])
        export_form.addRow("Кадр", self.layout_choice)
        self.export_mode = QComboBox()
        self.export_mode.addItem("Без повторного сжатия", "быстрый")
        self.export_mode.addItem("Точный монтаж", "точный")
        self.export_mode.setToolTip("Быстрое копирование зависит от ключевых кадров; границы приблизительные. Без фильтров звука.")
        export_form.addRow("Экспорт", self.export_mode)
        self.resolution = QComboBox()
        self.resolution.addItem("Как в исходнике", 0)
        for value in (2160, 1920, 1080, 720, 480):
            self.resolution.addItem(str(value), value)
        export_form.addRow("Высота, px", self.resolution)
        self.crf = QSpinBox()
        self.crf.setRange(0, 35)
        self.crf.setValue(18)
        self.crf.setToolTip("Меньше — выше качество и больше файл")
        export_form.addRow("CRF", self.crf)
        self.crop = QSlider(Qt.Orientation.Horizontal)
        self.crop.setRange(0, 100)
        self.crop.setValue(50)
        export_form.addRow("Кадр по горизонтали", self.crop)
        self.crop_y = QSlider(Qt.Orientation.Horizontal)
        self.crop_y.setRange(0, 100)
        self.crop_y.setValue(50)
        export_form.addRow("Кадр по вертикали", self.crop_y)
        self.webcam = {}
        for key, label, value in [("webcam_x", "Камера: положение X, %", 0),
                                   ("webcam_y", "Камера: положение Y, %", 0),
                                   ("webcam_w", "Камера: ширина, %", 30),
                                   ("webcam_h", "Камера: высота, %", 30)]:
            field = QSpinBox()
            field.setRange(5 if key.endswith(("w", "h")) else 0, 100)
            field.setValue(value)
            self.webcam[key] = field
            export_form.addRow(label, field)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0, 3)
        self.volume.setSingleStep(0.1)
        self.volume.setValue(1)
        export_form.addRow("Громкость ×", self.volume)
        self.fade = QDoubleSpinBox()
        self.fade.setRange(0, 2)
        self.fade.setSingleStep(0.1)
        self.fade.setValue(0)
        export_form.addRow("Плавные края звука, с", self.fade)
        il.addLayout(export_form)
        self.output_info = QLabel()
        self.output_info.setWordWrap(True)
        self.output_info.setObjectName("muted")
        il.insertWidget(2, self.output_info)
        self.burn = QCheckBox("Прожечь субтитры (+ отдельный SRT)")
        il.addWidget(self.burn)
        il.addWidget(button("Предпросмотр результата…", self.export_preview))
        self.reason = QLabel("Выберите момент, чтобы увидеть обоснование и источники.")
        self.reason.setWordWrap(True)
        self.reason.setObjectName("muted")
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        il.addWidget(self.reason)
        il.addWidget(button("Визуальные свидетельства…", self.show_evidence))
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
        label = QLabel("МОНТАЖ  ·  потяните ленту для перемотки")
        label.setObjectName("muted")
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        timeline_header.addWidget(label, 1)
        timeline_header.addWidget(button("Всё видео", self.edit_full_video))
        trim_button = QPushButton("Обрезать ▾")
        trim_menu = QMenu(trim_button)
        for text, action in [("Оставить до курсора", lambda: self.trim_at_cursor(True)),
                             ("Оставить после курсора", lambda: self.trim_at_cursor(False)),
                             ("Сохранить первую половину", lambda: self.edit_half(True)),
                             ("Сохранить вторую половину", lambda: self.edit_half(False)),
                             ("Разделить в позиции курсора", self.split_clip)]:
            trim_menu.addAction(text, action)
        trim_button.setMenu(trim_menu)
        timeline_header.addWidget(trim_button)
        timeline_header.addWidget(button("Вписать", self.fit_selection))
        timeline_header.addWidget(button("−", lambda: self.zoom(1.5)))
        timeline_header.addWidget(button("+", lambda: self.zoom(0.67)))
        layout.addLayout(timeline_header)
        self.overview, self.timeline = Timeline(True), Timeline()
        for timeline in (self.overview, self.timeline):
            timeline.scrubStarted.connect(self.begin_scrub)
            timeline.scrubbed.connect(lambda position, overview=timeline.overview: self.scrub_to(position, overview))
            timeline.scrubFinished.connect(self.end_scrub)
            timeline.boundaries.connect(self.apply_bounds)
            layout.addWidget(timeline)
        self.timeline_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scroll.setRange(0, 1000000)
        self.timeline_scroll.setFixedHeight(12)
        self.timeline_scroll.valueChanged.connect(self.scroll_timeline)
        layout.addWidget(self.timeline_scroll)
        bottom = QHBoxLayout()
        self.queue_label = QLabel("Очередь экспорта пуста")
        self.queue_label.setObjectName("muted")
        bottom.addWidget(self.queue_label, 1)
        bottom.addWidget(button("Очередь", self.show_queue))
        bottom.addWidget(button("Папка экспорта", self.choose_export_folder))
        bottom.addWidget(button("Экспортировать", self.enqueue))
        bottom.addWidget(button("Экспортировать и далее  ↵", self.export_next, True, "Ctrl+Enter"))
        layout.addLayout(bottom)
        for field in (self.layout_choice, self.export_mode, self.resolution):
            field.currentTextChanged.connect(self.options_changed)
        self.crf.valueChanged.connect(self.options_changed)
        self.crop.valueChanged.connect(self.options_changed)
        self.crop_y.valueChanged.connect(self.options_changed)
        self.volume.valueChanged.connect(self.options_changed)
        self.fade.valueChanged.connect(self.options_changed)
        for field in self.webcam.values():
            field.valueChanged.connect(self.options_changed)
        self.burn.toggled.connect(self.options_changed)
        self.layout_choice.currentTextChanged.connect(self.update_layout_fields)
        self.update_layout_fields()

    def update_layout_fields(self, *_):
        webcam = self.layout_choice.currentText() == "вебкамера + игра"
        for field in self.webcam.values():
            self.export_form.setRowVisible(field, webcam)
        self.inspector.setMinimumHeight(1080 if webcam else 790)

    def create_menu(self):
        menu = self.menuBar().addMenu("Проект")
        for text, action in [("Открыть видео…", self.choose_source), ("Открыть проект…", self.choose_project),
                             ("Переподключить исходник…", self.relink), ("Сохранить", self.save),
                             ("Импорт SRT/VTT…", self.import_captions), ("Экспорт всех оставленных", self.export_kept)]:
            item = menu.addAction(text)
            item.triggered.connect(action)
        menu.addAction("Кэш проекта…", self.cache_dialog)
        view = self.menuBar().addMenu("Вид")
        for text, widget in [("Расшифровка", self.transcript_panel), ("Инспектор клипа", self.inspector_scroll)]:
            visible = self.preferences.value("visible/"+text, text != "Расшифровка", type=bool)
            action = QAction(text, self, checkable=True, checked=visible)
            widget.setVisible(visible)
            action.toggled.connect(widget.setVisible)
            action.toggled.connect(lambda checked, key=text: self.preferences.setValue("visible/"+key, checked))
            view.addAction(action)
        edit = self.menuBar().addMenu("Правка")
        edit.addAction("Отменить", self.undo)
        edit.addAction("Повторить", self.redo)
        edit.addAction("Удалить момент", self.delete_clip)
        edit.addAction("Восстановить удалённый момент…", self.restore_deleted)
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
                   ("redo", "Ctrl+Shift+Z", self.redo), ("delete", "Delete", self.delete_clip)]
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
                                  idle=True, pause=True, volume=100,
                                  log_handler=lambda level, component, message: self.playbackError.emit(message.strip())
                                  if level in ("error", "fatal") else None)
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
        if self.worker.running() or (self.export_thread and self.export_thread.isRunning()):
            self.error("Дождитесь текущей фоновой операции или остановите её перед сменой проекта")
            return False
        if not self.stop_preview(wait=True):
            return False
        return True

    def open_source(self, path):
        if not self.can_switch():
            return
        try:
            data = probe(path)
            if not self.save():
                raise OSError("Не удалось сохранить текущий проект. Освободите место перед открытием другого файла")
            settings = {key: self.project.settings.get(key, value) for key, value in default_settings().items()}
            settings.update({key: self.project.settings[key] for key in ("export_dir", "export_options")
                             if key in self.project.settings})
            settings["transcribe"] = True
            project = Project(title=Path(path).stem, source=str(Path(path).resolve()), **data, settings=settings)
            folder = Path(settings["models_dir"]).parent / "projects" / (Path(path).stem[:70]+"-"+uid()[:8])
            store = ProjectStore(folder)
            project_lock = self.acquire_project_lock(folder)
            if self.project_lock:
                self.project_lock.unlock()
            self.project_lock = project_lock
            self.project, self.store = project, store
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
            if not (Path(folder)/"project.sqlite").is_file():
                raise ValueError("В выбранной папке нет проекта StreamCut (project.sqlite)")
            store = ProjectStore(folder)
            project_lock = self.acquire_project_lock(folder)
            if not self.save():
                raise OSError("Не удалось сохранить текущий проект. Освободите место перед переключением")
            project = store.load()
            project.settings = {key: self.project.settings.get(key, value) for key, value in default_settings().items()} | project.settings
            if self.project_lock and self.project_lock is not project_lock:
                self.project_lock.unlock()
            self.project_lock = project_lock
            self.project, self.store = project, store
            self.selected = None
            self.undo_stack.clear()
            self.redo_stack.clear()
            try:
                self.source_valid = Path(project.source).exists() and fingerprint(project.source) == project.fingerprint
            except OSError:
                self.source_valid = False
            self.load_media()
            if not self.source_valid:
                self.welcome.setText("Исходник перемещён или изменён.\nПроект → Переподключить исходник")
                self.welcome.show()
                self.statusBar().showMessage("Исходник недоступен. Правки сохранены; экспорт заблокирован до переподключения.")
            self.update_queue()
            QTimer.singleShot(200, self.run_queue)
        except Exception as exc:
            self.error(exc)

    def acquire_project_lock(self, folder):
        if self.store and self.project_lock and self.store.folder.resolve() == Path(folder).resolve():
            return self.project_lock
        lock = QLockFile(str(Path(folder)/"session.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise RuntimeError("Этот проект уже открыт в другом окне StreamCut. Закройте его там перед открытием")
        return lock

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
        self.pending_seek = self._seek_target = None
        self._last_preview_layout = None
        self._preview_generation += 1
        self._preview_request = None
        saved_position = self.project.settings.get("last_position")
        self.position = min(self.project.duration, max(0, float(saved_position or 0)))
        if self.player:
            self.player.command("stop")
            if self.source_valid:
                self.player.play(self.project.source)
                self.player.pause = True
                if self.project.audio_tracks:
                    self.player.aid = self.project.audio+1
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
        self.loading = True
        saved = self.project.settings.get("export_options", {})
        if not saved:
            saved = ExportOptions(mode="быстрый").model_dump()
        elif (self.project.settings.get("export_defaults_version", 0) < 2 and saved.get("height") == 1080 and
              saved.get("crf") == 20 and saved.get("layout") == "исходный" and saved.get("volume", 1) == 1 and
              not saved.get("subtitles") and saved.get("fade", 0) in (0, 0.1)):
            saved = saved | {"height": 0, "crf": 18, "mode": "быстрый", "fade": 0}
        saved_options = ExportOptions.model_validate(saved)
        self.project.settings["export_defaults_version"] = 2
        self.layout_choice.setCurrentText(saved_options.layout)
        self.export_mode.setCurrentIndex(max(0, self.export_mode.findData(saved_options.mode)))
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(saved_options.height)))
        self.crf.setValue(saved_options.crf)
        self.crop.setValue(round(saved_options.crop_x*100))
        self.crop_y.setValue(round(saved_options.crop_y*100))
        self.volume.setValue(saved_options.volume)
        self.fade.setValue(saved_options.fade)
        for key, field in self.webcam.items():
            field.setValue(round(getattr(saved_options, key)*100))
        self.burn.setChecked(saved_options.subtitles)
        self.loading = False
        for timeline in (self.overview, self.timeline):
            timeline.reset(self.project.duration, self.project.fps)
            timeline.clips = self.project.clips
            timeline.coverage = self.project.coverage
            timeline.invalidate()
        self.refresh_clips()
        self.refresh_transcript()
        if self.project.clips:
            previous = self.project.settings.get("selected_clip")
            self.select_clip(next((c for c in self.project.clips if c.id == previous), self.project.clips[0]))
        else:
            self.loading = True
            self.title.clear()
            self.note.clear()
            self.inpoint.setText(timecode(0))
            self.outpoint.setText(timecode(min(30, self.project.duration)))
            self.reason.setText("Добавьте момент вручную или запустите анализ")
            self.loading = False
        if saved_position is not None:
            self.seek(float(saved_position))
        self.options_changed()
        self.timeline_view_changed()

    def change_audio(self, index):
        if self.loading or index < 0:
            return
        if self.worker.running():
            self.stop_analysis()
        self.stop_preview()
        self._preview_generation += 1
        self._preview_request = None
        self.timeline._waves.clear()
        self.timeline._coarse_waves.clear()
        self.timeline.invalidate()
        if index != self.project.audio:
            archive = self.project.settings.setdefault("transcripts_by_track", {})
            archive[str(self.project.audio)] = [s.model_dump() for s in self.project.segments]
            self.project.segments = [Segment.model_validate(s) for s in archive.get(str(index), [])]
            self.refresh_transcript()
        self.project.audio = index
        if self.player and self.source_valid:
            self.player.aid = index+1
        self.save()
        self.preview_timer.start(80)

    def toggle_play(self):
        if self.player and self.source_valid:
            self.player.pause = not self.player.pause

    def seek(self, position):
        if self.player and self.source_valid:
            position = max(0, min(self.project.duration-0.001, position))
            self.pending_seek = position
            self._seek_exact = True
            self._seek_target, self._seek_started = position, time.monotonic()
            self.try_pending_seek()
            self.position = position
            self.timeline.ensure_visible(position)
            self.update_playhead()

    def try_pending_seek(self):
        if self.pending_seek is not None and self.player:
            try:
                self.player.command("seek", self.pending_seek, "absolute+exact" if self._seek_exact else "absolute+keyframes")
                self.pending_seek = None
            except Exception:
                # libmpv loads asynchronously; retry after file-loaded instead of showing a modal error.
                pass

    def begin_scrub(self):
        self.scrubbing = True
        self._scrub_latest = None
        self._scrub_was_playing = bool(self.player and not self.player.pause)
        if self.player:
            self.player.pause = True
        self.scrub_timer.start()

    def scrub_to(self, position, overview=False):
        if not self.source_valid:
            return
        self.position = max(0, min(self.project.duration-0.001, position))
        self._scrub_latest = self.position
        if overview:
            self.timeline.ensure_visible(self.position, center=True)
        self.update_playhead()

    def flush_scrub(self):
        if self._scrub_latest is not None and self.player and self.source_valid:
            self.pending_seek = self._scrub_latest
            self._seek_exact = False
            self._scrub_latest = None
            self.try_pending_seek()

    def end_scrub(self, position):
        self.scrub_timer.stop()
        self._scrub_latest = None
        self.scrubbing = False
        self.seek(position)
        if self.player:
            self.player.pause = not getattr(self, "_scrub_was_playing", False)
        self.preview_timer.start(80)

    def update_playhead(self):
        for timeline in (self.overview, self.timeline):
            timeline.position = self.position
            timeline.update()
        if not self.clock.hasFocus():
            self.clock.setText(timecode(self.position))

    def safe_seek(self, text):
        try:
            self.seek(parse_time(text))
        except ValueError as exc:
            self.error(exc)

    def frame_step(self, direction):
        if self.player and self.source_valid:
            self.player.pause = True
            try:
                self.player.command("frame-step" if direction > 0 else "frame-back-step")
            except Exception:
                self.statusBar().showMessage("Видео ещё загружается или покадровый переход недоступен", 5000)

    def set_speed(self, value):
        if self.player:
            self.player.speed = float(value.replace("×", ""))

    def tick(self):
        if self.player and self.source_valid and not self.scrubbing:
            try:
                self.try_pending_seek()
                actual = float(self.player.time_pos or 0)
                if self._seek_target is not None:
                    if abs(actual-self._seek_target) < 0.15 or time.monotonic()-self._seek_started > 5:
                        self._seek_target = None
                if self._seek_target is None:
                    self.position = actual
                if not self.player.pause:
                    self.timeline.ensure_visible(self.position)
                current = self.current()
                if self.loop.isChecked() and current and self.position >= current.end:
                    self.seek(current.start)
                elif self.loop.isChecked() and current:
                    for first, following in zip(current.spans, current.spans[1:]):
                        if first.end <= self.position < following.start:
                            self.seek(following.start)
                            break
            except Exception:
                pass
        self.update_playhead()

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
            content = " ".join([clip.title, clip.reason, clip.note]+[by_id.get(i, "") for i in clip.evidence]+
                               [q.get("text", "") for q in clip.quotes])
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
        self.timeline.invalidate()
        self.overview.invalidate()

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
        if clip.quotes:
            self.reason.setText(self.reason.text()+"\n\nРЕПЛИКИ\n"+"\n".join(
                timecode(q["start"])+"  "+q["text"] for q in clip.quotes))
        self.inpoint.setText(timecode(clip.start))
        self.outpoint.setText(timecode(clip.end))
        self.loading = False
        for timeline in (self.overview, self.timeline):
            timeline.start, timeline.end = clip.start, clip.end
            timeline.selection_spans = clip.spans
        self.seek(clip.start)
        self.options_changed()
        self.changed()

    def add_clip(self):
        if not self.source_valid:
            return self.error("Сначала откройте видео")
        start = min(self.position, max(0, self.project.duration-0.05))
        clip = Clip(title=f"Момент {len(self.project.clips)+1:02}", spans=[Span(start=start, end=min(self.project.duration, start+30))])
        self.project.clips.append(clip)
        self.undo_stack.append({"operation": "remove", "id": clip.id})
        self.redo_stack.clear()
        self.select_clip(clip)
        self.refresh_clips()

    def clear_selection(self):
        self.selected = None
        self.loading = True
        self.title.clear()
        self.note.clear()
        self.inpoint.setText(timecode(0))
        self.outpoint.setText(timecode(0))
        self.reason.setText("Выберите момент или нажмите «Всё видео», чтобы обрезать исходник")
        self.loading = False
        for timeline in (self.timeline, self.overview):
            timeline.start = timeline.end = 0
            timeline.selection_spans = []
            timeline.update()
        self.update_output_info()

    def delete_clip(self):
        clip = self.current()
        if not clip:
            return
        index = next((i for i, c in enumerate(self.clip_model.items) if c.id == clip.id), 0)
        inverse, _ = apply_edit(self.project, {"operation": "remove", "id": clip.id})
        self.undo_stack.append(inverse)
        self.redo_stack.clear()
        self.clear_selection()
        self.refresh_clips()
        if self.clip_model.items:
            self.select_clip(self.clip_model.items[min(index, len(self.clip_model.items)-1)])
            self.refresh_clips()
        self.changed()
        self.statusBar().showMessage("Момент удалён · Ctrl+Z — вернуть. Готовые файлы и очередь сохранены.", 6000)

    def clip_menu(self, point):
        index = self.clip_list.indexAt(point)
        if not index.isValid():
            return
        self.clip_list.setCurrentIndex(index)
        menu = QMenu(self)
        menu.addAction("Удалить момент · Delete", self.delete_clip)
        menu.exec(self.clip_list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def restore_deleted(self):
        if not self.project.deleted_clips:
            self.statusBar().showMessage("Удалённых моментов нет", 4000)
            return
        choices = [f"{i+1}. {c['title']}" for i, c in enumerate(self.project.deleted_clips)]
        choice, ok = QInputDialog.getItem(self, "Восстановить момент", "Удалённые моменты", choices, editable=False)
        if ok:
            data = self.project.deleted_clips[choices.index(choice)]
            inverse, identity = apply_edit(self.project, {"operation": "insert", "index": len(self.project.clips), "clip": data})
            self.undo_stack.append(inverse)
            self.redo_stack.clear()
            self.select_clip(next(c for c in self.project.clips if c.id == identity))
            self.refresh_clips()

    def source_range(self, start, end, title):
        if not self.source_valid or end <= start:
            return
        clip = Clip(title=title, spans=[Span(start=start, end=end)])
        self.project.clips.append(clip)
        self.undo_stack.append({"operation": "remove", "id": clip.id})
        self.redo_stack.clear()
        self.select_clip(clip)
        self.refresh_clips()
        return clip

    def edit_full_video(self):
        return self.source_range(0, self.project.duration, "Монтаж всего видео")

    def edit_half(self, first):
        middle = self.project.duration/2
        self.source_range(0 if first else middle, middle if first else self.project.duration,
                          "Первая половина" if first else "Вторая половина")
        self.fit_selection()

    def trim_at_cursor(self, before):
        cut = self.position
        if not self.current():
            self.edit_full_video()
        clip = self.current()
        if not clip:
            return
        spans = [Span(start=s.start, end=min(s.end, cut)) for s in clip.spans if s.start < cut] if before else [
            Span(start=max(s.start, cut), end=s.end) for s in clip.spans if s.end > cut]
        if not spans:
            self.statusBar().showMessage("Курсор находится за пределами сохраняемого участка", 5000)
            return
        self.set_fragments(spans)

    def split_clip(self):
        clip = self.current()
        if not clip:
            return
        cut = self.position
        if not clip.start < cut < clip.end:
            return
        left = [Span(start=s.start, end=min(s.end, cut)) for s in clip.spans if s.start < cut]
        right = [Span(start=max(s.start, cut), end=s.end) for s in clip.spans if s.end > cut]
        if not left or not right:
            return
        snapshot = clip.model_dump()
        other = clip.model_copy(deep=True)
        other.id, other.spans, other.title, other.edited = uid(), right, clip.title+" · 2", True
        clip.spans, clip.title, clip.edited = left, clip.title+" · 1", True
        index = self.project.clips.index(clip)
        self.project.clips.insert(index+1, other)
        self.undo_stack.append({"operation": "batch", "items": [{"operation": "remove", "id": other.id}, snapshot]})
        self.redo_stack.clear()
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
                if len(clip.spans) > 1:
                    spans = [s.model_copy() for s in clip.spans]
                    spans[0] = Span(start=start, end=spans[0].end)
                    spans[-1] = Span(start=spans[-1].start, end=end)
                else:
                    spans = [span]
                self.history()
                clip.spans = spans
                clip.edited = True
                if clip.status in ("в очереди", "экспортирован", "ошибка экспорта"):
                    clip.status = "оставлен"
            self.inpoint.setText(timecode(start))
            self.outpoint.setText(timecode(end))
            for timeline in (self.overview, self.timeline):
                timeline.start, timeline.end = start, end
                timeline.selection_spans = clip.spans
            self.changed()
            self.refresh_clips()
            self.update_output_info()
            return True
        except ValueError as exc:
            self.error(exc)
            return False

    def edit_bounds(self):
        if not self.loading:
            try:
                return self.apply_bounds(parse_time(self.inpoint.text()), parse_time(self.outpoint.text()))
            except ValueError as exc:
                self.error(exc)
                return False

    def mark(self, start):
        position = self.position
        clip = self.current()
        if not clip:
            self.edit_full_video()
            clip = self.current()
        if clip:
            self.apply_bounds(position if start else clip.start, clip.end if start else position)

    def edit_metadata(self, *_):
        clip = self.current()
        if clip and not self.loading:
            if (clip.title, clip.note, clip.status) != (self.title.text() or "Без названия", self.note.toPlainText(), self.status.currentText()):
                self.history()
            clip.title = self.title.text() or "Без названия"
            clip.note = self.note.toPlainText()
            clip.status = self.status.currentText()
            clip.edited = True
            self.changed()
            self.refresh_clips()

    def edit_fragments(self):
        clip = self.current()
        if not clip:
            return
        value = "\n".join(timecode(s.start)+" .. "+timecode(s.end) for s in clip.spans)
        text, ok = QInputDialog.getMultiLineText(self, "Составной клип", "Одна строка = фрагмент: начало .. конец.\nФрагменты идут по времени исходника, без пересечений.", value)
        if ok:
            try:
                spans = []
                for line in text.splitlines():
                    if line.strip():
                        a, b = line.split("..")
                        spans.append(Span(start=parse_time(a), end=parse_time(b)))
                self.set_fragments(spans)
            except ValueError as exc:
                self.error(exc)

    def set_fragments(self, spans):
        clip = self.current()
        if not clip:
            return
        candidate = Clip.model_validate(clip.model_dump() | {"spans": [s.model_dump() for s in spans]})
        if candidate.end > self.project.duration:
            raise ValueError("Фрагмент выходит за конец исходника")
        self.history()
        clip.spans = candidate.spans
        clip.edited = True
        if clip.status in ("в очереди", "экспортирован", "ошибка экспорта"):
            clip.status = "оставлен"
        self.select_clip(clip)
        self.refresh_clips()

    def cut_inside(self):
        clip = self.current()
        if not clip:
            return
        start = max(clip.start, min(self.position, clip.end-0.04))
        text, ok = QInputDialog.getText(self, "Удалить участок из клипа", "Начало .. конец (в исходнике)",
            text=timecode(start)+" .. "+timecode(min(clip.end, start+5)))
        if ok:
            try:
                a, b = text.split("..")
                self.set_fragments(remove_range(clip.spans, Span(start=parse_time(a), end=parse_time(b))))
            except ValueError as exc:
                self.error(exc)

    def undo(self):
        self.restore_edit(self.undo_stack, self.redo_stack)

    def redo(self):
        self.restore_edit(self.redo_stack, self.undo_stack)

    def restore_edit(self, source, destination):
        if source:
            inverse, selected = apply_edit(self.project, source.pop())
            if inverse:
                destination.append(inverse)
            clip = next((c for c in self.project.clips if c.id == selected), self.current())
            if clip:
                self.select_clip(clip)
            else:
                self.clear_selection()
            self.refresh_clips()
            self.changed()

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
                self.project.settings["last_position"] = self.position
                self.project.settings["selected_clip"] = self.selected
                self.store.save(self.project)
                self.preferences.setValue("recent_project", str(self.store.folder))
                self.statusBar().showMessage("Сохранено · "+str(self.store.folder), 3000)
                return True
            except Exception as exc:
                self.analysis_label.setText("Ошибка сохранения: "+str(exc))
                return False
        return True

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
            segment.edited = True
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
            self.remember_settings()
            self.save()
        dialog.deleteLater()

    def models(self):
        if self.worker.running():
            return self.error("Остановите анализ перед загрузкой или выгрузкой моделей")
        dialog = ModelsDialog(self.project.settings, self)
        dialog.exec()
        self.remember_settings()
        self.save()
        dialog.deleteLater()

    def remember_settings(self):
        values = {key: self.project.settings.get(key, value) for key, value in default_settings().items()}
        self.preferences.setValue("analysis_settings", json.dumps(values, ensure_ascii=False))

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
        self.analyze_button.setEnabled(not self.worker.running())
        self.pause_button.setEnabled(self.worker.running())

    def analyze_selection(self):
        clip = self.current()
        if clip:
            if self.worker.running():
                return
            if not self.source_valid or not self.store:
                self.error("Переподключите исходник перед анализом выделения")
                return
            self.selection_overrides = {"visual_start": clip.start, "visual_end": clip.end, "transcribe": False,
                                        "text_search": False, "visual": "подробный"}
            self.settings_before_selection = {key: (key in self.project.settings, self.project.settings.get(key))
                                              for key in self.selection_overrides}
            self.project.settings.update(self.selection_overrides)
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
            text = event["text"]
            if event.get("eta_seconds") is not None:
                text += " · осталось ≈ "+timecode(event["eta_seconds"])[:-4]
            self.analysis_label.setText(text)
            self.analysis_label.setToolTip(event["text"])
        if "progress" in event:
            self.analysis_progress.setValue(int(event["progress"]*100))
        if kind == "segments":
            if "block_start" in event:
                self.project.segments = [s for s in self.project.segments if s.edited or not (
                    event["block_start"] <= s.start < event["block_end"])]
            ids = {s.id for s in self.project.segments}
            self.project.segments.extend(Segment.model_validate(s) for s in event["items"] if s["id"] not in ids)
            self.refresh_transcript()
        if kind in ("clips", "visual"):
            for data in event["items"]:
                candidate = Clip.model_validate(data)
                if not candidate_was_deleted(self.project, candidate):
                    merge_candidate(self.project.clips, candidate)
            self.refresh_clips()
        if kind == "visual":
            existing = {e["id"] for e in self.project.visual_evidence}
            self.project.visual_evidence.extend(e for e in event.get("evidence", []) if e["id"] not in existing)
            self.project.coverage.extend(Span(start=t, end=min(self.project.duration, t+0.001)) for t in event["samples"])
            for timeline in (self.overview, self.timeline):
                timeline.coverage = self.project.coverage
                timeline.invalidate()
        if kind == "model":
            self.project.settings.setdefault("model_versions", {})[event["name"]] = event["revision"]
            self.save()
        if "checkpoint" in event:
            self.project.checkpoints.setdefault("analysis", []).append(event["checkpoint"])
            self.save()

    def analysis_finished(self):
        if hasattr(self, "settings_before_selection"):
            for key, (present, value) in self.settings_before_selection.items():
                if self.project.settings.get(key) == self.selection_overrides[key]:
                    if present:
                        self.project.settings[key] = value
                    else:
                        self.project.settings.pop(key, None)
            del self.settings_before_selection
            del self.selection_overrides
        self.analyze_button.setEnabled(True)
        self.analyze_button.setText("Продолжить анализ")
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Пауза")
        self.save()

    def show_evidence(self):
        clip = self.current()
        if not clip:
            return
        items = [e for e in self.project.visual_evidence if e["id"] in clip.evidence]
        if not items:
            self.statusBar().showMessage("У этого момента нет сохранённых визуальных свидетельств", 5000)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Кадры и обоснование модели · проверьте событие в плеере")
        layout = QVBoxLayout(dialog)
        for item in items:
            text = QLabel(timecode(item["seconds"])+" · "+item["description"])
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(text)
            image = QLabel()
            path = Path(item["path"])
            if not path.is_absolute():
                path = self.store.folder/path
            image.setPixmap(QPixmap(str(path)).scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio))
            layout.addWidget(image)
        dialog.exec()

        dialog.deleteLater()

    def cache_dialog(self):
        if not self.store:
            return
        if not self.can_switch():
            return
        folder = self.store.folder/"cache"
        files = [p for p in folder.iterdir() if p.is_file() and (
            p.name in ("audio.wav", "preview.wav", "analysis.json") or
            (p.name.startswith("preview-") and p.suffix == ".jpg") or
            (p.name.endswith("preview.mp4")))]
        timeline_cache = folder/"timeline-v2"
        if timeline_cache.exists():
            files += [p for p in timeline_cache.glob("*/*") if p.is_file() and
                      p.resolve().is_relative_to(folder.resolve()) and
                      (p.name.startswith("frame-") and p.suffix == ".jpg" or
                       p.name.startswith("wave-a") and p.suffix in (".json", ".wav"))]
        size = sum(p.stat().st_size for p in files)
        if QMessageBox.question(self, "Кэш проекта", f"Временные данные: {size/1024**2:.1f} МБ.\n"
            "Очистить волны, миниатюры и временные предпросмотры?\n"
            "Расшифровка, правки, свидетельства, исходник и готовые клипы сохранятся.") == QMessageBox.StandardButton.Yes:
            protected = {Path(j.destination).resolve() for j in self.project.jobs
                         if not j.destination.endswith("preview.mp4")}
            protected.add(Path(self.project.source).resolve())
            for path in files:
                if path.resolve() not in protected:
                    path.unlink(missing_ok=True)
            self.statusBar().showMessage("Временный кэш очищен")

    def choose_export_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка готовых клипов", self.project.settings.get("export_dir", ""))
        if folder:
            self.project.settings["export_dir"] = folder
            self.save()

    def export_options(self):
        return ExportOptions(mode=self.export_mode.currentData(), layout=self.layout_choice.currentText(),
                             crf=self.crf.value(), height=self.resolution.currentData(),
                             crop_x=self.crop.value()/100, crop_y=self.crop_y.value()/100,
                             volume=self.volume.value(), fade=self.fade.value(), subtitles=self.burn.isChecked(),
                             **{key: field.value()/100 for key, field in self.webcam.items()})

    def options_changed(self, *_):
        if not self.loading:
            options = self.export_options()
            requires_encoding = (options.layout != "исходный" or options.height or options.subtitles or
                                 options.volume != 1 or options.fade or
                                 (self.current() and len(self.current().spans) > 1))
            if self.export_mode.currentData() == "быстрый" and (requires_encoding or self.sender() is self.crf):
                self.export_mode.blockSignals(True)
                self.export_mode.setCurrentIndex(self.export_mode.findData("точный"))
                self.export_mode.blockSignals(False)
            self.project.settings["export_options"] = self.export_options().model_dump()
            self.update_output_info()
            self.crf.setEnabled(self.export_mode.currentData() == "точный")
            if hasattr(self, "format_timer"):
                self.format_timer.start(120)
            self.changed()

    def set_output_layout(self, layout):
        self.layout_choice.setCurrentText(layout)
        self.resolution.setCurrentIndex(self.resolution.findData(0))
        if layout == "исходный" and not self.burn.isChecked() and self.volume.value() == 1 and not self.fade.value():
            self.export_mode.setCurrentIndex(self.export_mode.findData("быстрый"))
        self.options_changed()

    def update_output_info(self):
        options = self.export_options()
        width, height = output_dimensions(options, self.project.width, self.project.height)
        self.original_button.setChecked(options.layout == "исходный")
        self.shorts_button.setChecked(options.layout == "9:16")
        mode = "без повторного сжатия; границы по ключевым кадрам" if options.mode == "быстрый" else (
            "точные границы; видео без потерь" if not options.crf else "точные границы; высокое качество")
        clip = self.current()
        duration = f"\nСохранится {timecode(clip.duration)[:-4]} · фрагментов: {len(clip.spans)}" if clip else ""
        self.output_info.setText(f"Результат: {width} × {height}\n{mode}"+duration)

    def apply_output_preview(self):
        if not self.player or not self.source_valid:
            return
        try:
            options = self.export_options()
            layout_key = (self.project.fingerprint, options.layout, options.crop_x, options.crop_y,
                          options.webcam_x, options.webcam_y, options.webcam_w, options.webcam_h)
            if layout_key != self._last_preview_layout and options.layout == "исходный":
                self.player.vf = []
                self.player.hwdec = "auto-safe"
            elif layout_key != self._last_preview_layout:
                # Composition preview has the same geometry at a lighter display
                # size. Export retains the resolution shown in the inspector.
                options.height = min(640, output_dimensions(options, self.project.width, self.project.height)[1])
                graph = video_filter(options, self.project.width, self.project.height)
                self.player.hwdec = "auto-copy"
                self.player.command("vf", "set", f"lavfi=%{len(graph)}%{graph}")
            self._last_preview_layout = layout_key
            self.player.volume = self.volume.value()*100
        except Exception as exc:
            self.statusBar().showMessage("Не удалось обновить предпросмотр компоновки: "+str(exc), 7000)

    def enqueue(self, checked=False, clip=None, destination=None):
        clip = clip or self.current()
        if not clip or not self.source_valid:
            self.error("Выберите момент с доступным исходником")
            return False
        if not self.edit_bounds():
            return False
        folder = self.project.settings.get("export_dir")
        if not folder and not destination:
            self.choose_export_folder()
            folder = self.project.settings.get("export_dir")
            if not folder:
                return False
        self.options_changed()
        safe = lambda s: re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")[:140]
        name = self.project.settings.get("export_template", "{project}_{time}_{title}")
        for token, value in {"project": self.project.title, "time": timecode(clip.start).replace(":", "-"), "title": clip.title}.items():
            name = name.replace("{"+token+"}", safe(value)[:45])
        name = safe(name) or "StreamCut"
        path = Path(destination) if destination else Path(folder)/(name+"_"+uid()[:6]+".mp4")
        job = ExportJob(clip=clip.model_copy(deep=True), source=self.project.source,
                        fingerprint=self.project.fingerprint, audio=self.project.audio,
                        preview=bool(destination and str(destination).endswith("preview.mp4")),
                        options=self.export_options(), segments=[s.model_copy(deep=True) for s in self.project.segments
                            if s.end > clip.start and s.start < clip.end], destination=str(path))
        self.project.jobs.append(job)
        if not job.preview:
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
        if not job.preview and clip and clip.spans == job.clip.spans and clip.title == job.clip.title and clip.status != "отклонён":
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
        dialog.deleteLater()

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
        timer.stop()
        dialog.deleteLater()

    def export_preview(self):
        if self.store and self.current():
            destination = self.store.folder / "cache" / (uid()[:8]+"preview.mp4")
            self.enqueue(destination=str(destination))
            self.statusBar().showMessage("Предпросмотр рендерится с настройками экспорта; файл откроется после готовности")

    def zoom(self, factor):
        self.timeline.zoom_by(factor)

    def fit_selection(self):
        clip = self.current()
        if clip:
            self.timeline.window = min(self.project.duration, max(1, (clip.end-clip.start)*1.15))
            self.timeline.view_start = max(0, min(self.project.duration-self.timeline.window,
                                                 clip.start-(self.timeline.window-(clip.end-clip.start))/2))
            self.timeline_view_changed()
            self.timeline.update()

    def scroll_timeline(self, value):
        self.timeline.pan_to(value/1000000*max(0, self.project.duration-self.timeline.window))

    def timeline_view_changed(self):
        start, end = self.timeline.limits()
        self.overview.viewport = start, end
        self.overview.update()
        self.timeline_scroll.blockSignals(True)
        maximum = max(0, self.project.duration-(end-start))
        self.timeline_scroll.setValue(round(start/maximum*1000000) if maximum else 0)
        self.timeline_scroll.setPageStep(max(1, round((end-start)/max(1, self.project.duration)*1000000)))
        self.timeline_scroll.blockSignals(False)
        self._preview_request = (round(start, 3), round(end, 3), self.timeline.width(), self._preview_generation)
        if self.preview_thread and getattr(self.preview_thread, "request_key", None) != self._preview_request:
            self.preview_thread.cancel.set()
        self.preview_timer.start(130 if self.scrubbing else 60)

    def update_preview(self):
        if not self.store or not self.source_valid:
            return
        start, end = self.timeline.limits()
        generation = self._preview_generation
        request = (round(start, 3), round(end, 3), self.timeline.width(), generation)
        self._preview_request = request
        if self.preview_thread:
            if getattr(self.preview_thread, "request_key", None) != request:
                self.preview_thread.cancel.set()
            return
        thread = PreviewThread(self.project.source, self.project.audio if self.project.audio_tracks else -1,
            start, end, self.store.folder/"cache", self.project.fingerprint, self.project.duration,
            self.timeline.width(), self.position)
        self.preview_thread = thread
        thread.request_key = request
        self.timeline.loading = True
        self.timeline.update()
        thread.frameReady.connect(lambda stamp, step, data: self.preview_frame(generation, stamp, step, data))
        thread.waveReady.connect(lambda chunk, peaks: self.preview_wave(generation, chunk, peaks))
        thread.failed.connect(lambda text: self.statusBar().showMessage("Таймлайн: "+text, 6000))
        thread.finished.connect(lambda: self.preview_finished(thread, request))
        thread.start()

    def preview_finished(self, thread, request):
        if self.preview_thread is thread:
            self.preview_thread = None
            self.timeline.loading = False
            self.timeline.update()
            if self._preview_request is not None and (self._preview_request != request or thread.cancel.is_set()):
                self.preview_timer.start(30)
        thread.deleteLater()

    def preview_frame(self, generation, stamp, step, data):
        if generation == self._preview_generation:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.timeline.add_frame(stamp, step, pixmap)

    def preview_wave(self, generation, chunk, peaks):
        if generation == self._preview_generation:
            self.timeline.add_wave(chunk, peaks)

    def stop_preview(self, wait=False):
        self.preview_timer.stop()
        self._preview_request = None
        thread = self.preview_thread
        if thread:
            thread.cancel.set()
            if wait:
                thread.wait(2000)
                if thread.isRunning():
                    return False
                self.preview_thread = None
                self.timeline.loading = False
                self.timeline.update()
        return True

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
        if not self.stop_preview(wait=True):
            event.ignore()
            return
        if not self.save():
            event.ignore()
            return
        self.preferences.setValue("geometry", self.saveGeometry())
        self.preferences.setValue("panels", self.splitter.saveState())
        if self.player:
            self.player.terminate()
        if self.project_lock:
            self.project_lock.unlock()
        event.accept()
