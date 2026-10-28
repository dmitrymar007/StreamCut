# Сторонние компоненты

StreamCut использует PySide6/Qt, python-mpv/libmpv, FFmpeg, faster-whisper, CTranslate2, PyAV, Pydantic, httpx и их зависимости. Лицензии этих компонентов сохраняют силу; лицензии библиотек и моделей не заменяются лицензией приложения.

- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) — LGPL/GPL или коммерческая лицензия в зависимости от компонентов.
- [libmpv](https://github.com/mpv-player/mpv), Windows-сборки [shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake).
- [FFmpeg](https://ffmpeg.org/), Windows-сборки [BtbN](https://github.com/BtbN/FFmpeg-Builds). Скрипт выбирает GPL-вариант с libx264.
- [7-Zip](https://www.7-zip.org/) — локальная распаковка BCJ2 при подготовке окружения.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2), [Whisper](https://github.com/openai/whisper).
- [Qwen3.5](https://ollama.com/library/qwen3.5) — веса скачиваются отдельно через Ollama.

Бинарные зависимости и веса не входят в Git. Скрипт установки сохраняет доступные лицензионные файлы релизов в vendor и фактические URL/SHA256 в manifest.json. Перед распространением сборки следует учитывать лицензии включённых компонентов и моделей.
