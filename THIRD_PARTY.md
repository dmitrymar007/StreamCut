# Сторонние компоненты

Исходники StreamCut распространяются под GNU GPLv3; полный текст — в `LICENSE`. В установщик и portable входят неизменённые библиотеки с собственными лицензиями. Снимок исходников приложения, зависимости, скрипты сборки и контрольные суммы предоставляются в релизе и репозитории.

StreamCut использует PySide6/Qt, python-mpv/libmpv, FFmpeg, faster-whisper, CTranslate2, PyAV, Pydantic, httpx и их зависимости. Лицензии этих компонентов сохраняют силу; лицензии библиотек и моделей не заменяются лицензией приложения.

- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) — LGPL/GPL или коммерческая лицензия в зависимости от компонентов.
- [libmpv](https://github.com/mpv-player/mpv), Windows-сборки [shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake).
- [FFmpeg](https://ffmpeg.org/), Windows-сборки [BtbN](https://github.com/BtbN/FFmpeg-Builds). Скрипт выбирает GPL-вариант с libx264.
- [7-Zip](https://www.7-zip.org/) — локальная распаковка BCJ2 при подготовке окружения.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2), [Whisper](https://github.com/openai/whisper).
- [Qwen3.5](https://ollama.com/library/qwen3.5) — веса скачиваются отдельно через Ollama.
- [Ollama](https://github.com/ollama/ollama) — MIT; официальный установщик скачивается отдельно при первой подготовке.
- [NVIDIA cuBLAS/CUDA](https://docs.nvidia.com/cuda/eula/index.html), [cuDNN](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html) — собственные лицензии NVIDIA. Windows-колёса загружаются с PyPI отдельно при подготовке NVIDIA, не включены в установщик. Тексты лицензий извлекаются рядом с DLL в `%LOCALAPPDATA%\StreamCut\runtime\…\licenses`.
- [NSIS](https://nsis.sourceforge.io/License) — компилятор установщика, не требуется установленной программе.

`scripts/collect_notices.py` сохраняет лицензии установленных Python-компонентов и их версии в `_internal/third-party-notices`, включая `inventory.json` со ссылками на исходники. Лицензии исходных сборок BtbN и shinchiro также включены. Qt и libmpv подключены динамически; их DLL находятся в `_internal`. Редактор может использовать совместимые заменяющие сборки библиотек.

Медиасборки этой версии: FFmpeg `N-128121-g86444dd368-20261027` ([исходники](https://github.com/FFmpeg/FFmpeg/tree/86444dd368), [скрипты сборки и зависимости](https://github.com/BtbN/FFmpeg-Builds)); libmpv `e471496c56` ([исходники mpv](https://github.com/mpv-player/mpv/tree/e471496c56), [скрипты сборки и зависимости](https://github.com/shinchiro/mpv-winbuild-cmake)). Версии и лицензии библиотек внутри статических медиасборок определяются их upstream-конфигурацией; ссылки и исходные лицензионные файлы сохраняются вместе с дистрибутивом.

Бинарные зависимости, модели и пользовательские видео не входят в Git. Подготовка медиабиблиотек сохраняет фактические URL/SHA256 в vendor/manifest.json. Для CUDA и Ollama контрольные суммы и версии находятся в assets/*manifest.json и assets/ollama-installer.json. Установщик не загружает платные облачные сервисы.
