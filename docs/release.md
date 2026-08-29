# Сборка и распространение Konspekt

## Матрица поддерживаемых платформ

| Платформа | Архитектура | Системные требования | Веб-компонент | Хранилище секретов |
|---|---|---|---|---|
| Windows 10 / 11 | x86_64 (x64) | Windows 10 build 19041+ | Microsoft Edge WebView2 | Windows Credential Manager |
| macOS 13+ (Ventura) | Apple Silicon (arm64) | macOS 13.0+ | WKWebView (Cocoa) | macOS Keychain |
| macOS 13+ (Ventura) | Intel (x86_64) | macOS 13.0+ | WKWebView (Cocoa) | macOS Keychain |

> Примечание для macOS: собираются отдельные бинарные пакеты `.app`/дистрибутивы под `arm64` и `x86_64` во избежание проблем с нативными библиотеками машинного обучения.

## Windows

Запусти `Konspekt.exe` двойным кликом.

Не нужны Python, `.venv` или дополнительные `.bat`-файлы. Не перемещай `Konspekt.exe` отдельно: ему нужна папка `_internal` рядом с ним.

В выпуск уже включены:
- FFmpeg для извлечения аудио и кадров;
- Tesseract OCR для текста на экране;
- Faster-Whisper и его локальные зависимости;
- pywebview для окна авторизации WebView2;
- иконка Konspekt для окна и файла `.exe`.

Материалы лекций сохраняются локально в `%LOCALAPPDATA%\Konspekt`.

## macOS

На macOS приложение запускается как `Konspekt.app` или через терминал.
Материалы лекций и настройки сохраняются в `~/Library/Application Support/Konspekt`, а временные кэши — в `~/Library/Caches/Konspekt`.
Авторизация OpenAI использует системный WKWebView, а ключи API хранятся в защищённом системном Keychain.

## Сборка релизных пакетов

Для сборки используй скрипт:
```bash
python scripts/build_package.py
```
После сборки запусти проверку:
```bash
python scripts/smoke_package.py
```

## Подпись и нотаризация (macOS Release)

Для создания production-релиза для macOS требуется действующий Apple Developer ID:

1. **Подпись бинарников и фреймворков внутри `.app`:**
   ```bash
   codesign --force --deep --options runtime \
     --entitlements packaging/entitlements.plist \
     --sign "Developer ID Application: Your Name (TEAM_ID)" \
     "dist/Konspekt.app"
   ```

2. **Создание дистрибутивного DMG:**
   ```bash
   hdiutil create -volname "Konspekt" -srcfolder "dist/Konspekt.app" -ov -format UDZO "dist/Konspekt-macOS.dmg"
   codesign --force --sign "Developer ID Application: Your Name (TEAM_ID)" "dist/Konspekt-macOS.dmg"
   ```

3. **Отправка на нотаризацию в Apple Notary Service:**
   ```bash
   xcrun notarytool submit "dist/Konspekt-macOS.dmg" \
     --keychain-profile "notary-profile" \
     --wait
   ```

4. **Прикрепление билета нотаризации (Stapling):**
   ```bash
   xcrun stapler staple "dist/Konspekt-macOS.dmg"
   spctl --assess -vv --type install "dist/Konspekt-macOS.dmg"
   ```

## Подпись исполняемых файлов (Windows Authenticode)

Для подписания `Konspekt.exe` на Windows используй `signtool`:
```cmd
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f certificate.pfx /p Password "dist\Konspekt\Konspekt.exe"
signtool verify /pa /v "dist\Konspekt\Konspekt.exe"
```

## Общие возможности

При первой обработке Faster-Whisper скачает выбранную открытую модель на компьютер. Это не API-вызов и не требует токенов. Интернет также нужен для загрузки публичной BBB-записи.

Для более быстрой работы по умолчанию выбрана модель Whisper `base` и кадр экрана раз в 60 секунд. Скорость, точность и OCR можно изменить в разделе «Настройки». После сбоя кнопка «Повторить» продолжает работу с уже готовых файлов.

API не обязателен. Если добавить ключ OpenAI или DeepSeek в настройках, приложение сможет отправить провайдеру только текстовый контекст и автоматически сохранить ответ как `lesson.md`. Аудио, видео и кадры в API не отправляются; ключ защищён системным хранилищем.

Без API-ключа можно войти через личный ChatGPT. Konspekt откроет одно отдельное окно только для ручной авторизации, затем покажет доступные аккаунту модели Codex и автоматически сохранит `lesson.md`. Чат-интерфейс `chatgpt.com` в приложении не открывается, DOM и cookies не считываются.

Этот путь использует официальный [Codex app-server](https://learn.chatgpt.com/docs/app-server) со статусом Experimental. Он требует установленный Codex CLI, может меняться между версиями и не гарантирует доступ к обычным моделям из интерфейса ChatGPT. OpenAI/DeepSeek API остаются отдельными способами подключения со своими ключами и биллингом.
