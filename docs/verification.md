# Верификация ветки `work/konspekt-cross-platform`

## Репозиторий и проверяемая ветка

- Репозиторий: `https://github.com/ppmarkek/TSI-Remote.git`
- Ветка: `work/konspekt-cross-platform`
- Pull request: `https://github.com/ppmarkek/TSI-Remote/pull/1`
- Поддерживаемый Python для исходного запуска и тестов: 3.10–3.12.
- Пакеты выпускаются для Windows 10/11 x64 и macOS 13+; CI собирает их на актуальных Windows и macOS runners.

Проверять следует новый чистый клон, а не каталог разработчика:

```bash
git clone https://github.com/ppmarkek/TSI-Remote.git
cd TSI-Remote
git switch --track origin/work/konspekt-cross-platform
```

Перед каждой повторной проверкой убедитесь, что рабочее дерево чистое и HEAD соответствует коммиту PR:

```bash
git status --short
git rev-parse HEAD
git diff --check
```

## Установка окружения

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,local-ai]"
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,local-ai]"
```

### Windows Command Prompt

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,local-ai]"
```

`uv` также допустим, но extras обязательны:

```bash
uv sync --extra dev --extra local-ai
```

## Автоматические проверки

### macOS

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
ruff check .
ruff format --check .
git diff --check
```

### Windows PowerShell

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
git diff --check
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
```

### Windows Command Prompt

```cmd
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPATH=src
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
git diff --check
set PYTHONPATH=
set PYTHONDONTWRITEBYTECODE=
```

Ruff намеренно не изменяет allowlist-файл `src/konspekt/bbb_import.py`; исключение должно присутствовать и для lint, и для formatter в `pyproject.toml`.

## Сборка и smoke-test пакета

### macOS

До сборки должен быть доступен Tesseract, например через Homebrew:

```bash
brew install tesseract
python scripts/build_package.py
python scripts/smoke_package.py
```

Ожидаемые файлы:

- `dist/Konspekt.app`
- `dist/Konspekt.dmg`

Принудительная GUI-проверка в интерактивной сессии:

```bash
KONSPEKT_GUI_SMOKE=1 python scripts/smoke_package.py
```

### Windows PowerShell

Установите Tesseract и убедитесь, что его каталог доступен через `PATH`, затем выполните:

```powershell
python scripts/build_package.py
python scripts/smoke_package.py
$env:KONSPEKT_GUI_SMOKE="1"
python scripts/smoke_package.py
Remove-Item Env:KONSPEKT_GUI_SMOKE -ErrorAction SilentlyContinue
```

Ожидаемый файл:

- `dist\Konspekt\Konspekt.exe`

`build_package.py` обязан вернуть ненулевой код, если ожидаемый `.app`, DMG или `.exe` не создан либо пуст. `smoke_package.py` запускается с очищенным `PATH`, чтобы подтвердить наличие упакованных FFmpeg/Tesseract, проверяет JSON diagnostics и, при доступной интерактивной сессии, жизненный цикл GUI.

## Проверка GitHub Actions

Во вкладке **Actions** должны быть зелёными:

1. unit tests на Windows, macOS и Linux для всех версий Python из matrix;
2. `ruff check .` и `ruff format --check .`;
3. Windows package build + smoke-test;
4. macOS package build + smoke-test;
5. загрузка непустых артефактов `package-windows-latest` и `package-macos-14`.

После зелёного workflow скачайте оба артефакта. Smoke-test из Actions не заменяет ручную проверку интерфейса.

## Ручная проверка скачанных артефактов

Проводите её отдельно на Windows и macOS.

### Импорт и обработка

1. Импортируйте рабочую BBB playback-ссылку.
2. Импортируйте локальный `.mp3`, `.mp4` или `.m4a`.
3. Для локального файла подтвердите, что копирование идёт через `.part`, а повторный импорт повреждённого кэша восстанавливает исходные bytes.
4. Запустите подготовку лекции и отмените её во время скачивания, FFmpeg и распознавания.
5. Убедитесь, что дочерние процессы завершились, UI не завис и повторный запуск возобновляет допустимые этапы.
6. Проверьте создание и повторное использование `lecture-manifest.json`; изменённые параметры или bytes должны инвалидировать соответствующий этап.

### Приватность и внешние провайдеры

1. Соберите пакет контекста.
2. Подмените копию пакета тестовыми URL, absolute path, UUID, meeting ID и фиктивным токеном.
3. Убедитесь, что DeepSeek handoff и API/ChatGPT generation блокируются до открытия браузера или отправки.
4. Повторите DeepSeek-проверку после изменения файла между подготовкой handoff и нажатием запуска: точный meeting ID и исходный URL также должны блокироваться.
5. Проверьте, что сообщения об ошибке не раскрывают сам секрет.

### Экспорт HTML/PDF

1. Создайте конспект с кириллицей, латиницей, заголовками, списками и таймкодами.
2. Экспортируйте HTML и откройте его без сети.
3. Экспортируйте PDF и откройте его в системном viewer и ещё одном независимом viewer.
4. Кириллица должна отображаться одинаково; PDF fallback не должен зависеть от подстановки Helvetica/Identity-H viewer-ом.

### Библиотека и корзина

1. Проверьте поиск, фильтры состояния/даты и сортировку.
2. Переименуйте лекцию и перезапустите приложение.
3. Откройте папку лекции через системный file manager.
4. Переместите лекцию в корзину, восстановите её и повторно перезапустите приложение.
5. Создайте коллизию каталога перед восстановлением: приложение должно остановить операцию и не удалять существующие данные.
6. Повредите `trash.json` полем `folder_name` с `../` или absolute path: восстановление должно быть заблокировано.
7. Добавьте symlink в `slides/` на внешний файл и экспортируйте ZIP: внешний файл не должен попасть в архив.

### Интерфейс

Проверьте:

- библиотеку и пустое состояние;
- настройки и защищённое хранение API-ключа;
- reader, оглавление и таймкоды;
- кнопки отмены, retry и возврата;
- экран корзины и восстановление;
- системные сочетания `⌘` на macOS и `Ctrl` на Windows;
- открытие папки лекции;
- отсутствие зависаний при закрытии приложения во время активной операции.

## Обязательная процедура при ошибке

При любом непройденном тесте, Ruff error, failed build, failed smoke-test или ручном дефекте:

1. merge и выпуск немедленно блокируются;
2. фиксируется точный коммит, ОС, Python и воспроизводящий сценарий;
3. исправляется первопричина, а не только тест;
4. подтверждается, что `src/konspekt/bbb_import.py` по-прежнему байт-в-байт совпадает с коммитом `6af6eb33ebe9439ed47043aac26ced4cf4c3fdde`;
5. весь цикл unit tests, Ruff, build, smoke-test и затронутые ручные сценарии выполняется заново;
6. merge разрешается только после зелёного CI, появления обоих артефактов и закрытия всех review-замечаний.
