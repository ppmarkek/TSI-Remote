---
name: "Конспект"
description: "A calm study workspace: library, reader with timestamps, flashcards."
colors:
  bg: "#F6F7F9"
  sidebar: "#EEF0F3"
  surface: "#FFFFFF"
  border: "#E4E7EC"
  text: "#1D2129"
  text-2: "#5A6170"
  text-3: "#8A90A0"
  accent: "#4F5BD5"
  accent-soft: "#EEF0FD"
  success: "#1E8E5A"
  warning: "#B26B00"
  danger: "#D14343"
  mark: "#FFE58A"
colors-dark:
  bg: "#15171C"
  sidebar: "#1B1E24"
  surface: "#1F2229"
  border: "#2E333D"
  text: "#E6E8EC"
  text-2: "#A3A9B6"
  accent: "#7C86F2"
typography:
  ui: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Arial"
  display: "Segoe UI Variable Display, Segoe UI, system-ui"
  reading-serif: "Charter, Iowan Old Style, Georgia, Cambria, serif"
  mono: "Cascadia Mono, Consolas, SF Mono, Menlo"
  base-size: "14px"
rounded:
  small: "6px"
  control: "10px"
  large: "14px"
layout:
  sidebar: "236px"
  window: "1280 × 820, minimum 960 × 620"
  reading-measure: "720px"
---

# Design System: Конспект

## Overview

**Creative North Star: «Спокойное место для учёбы».** The app exists so a student
can turn a lecture into something they can read in fifteen minutes and remember a
week later. Every screen answers one question: *what should I do next with this
lecture?*

The main interface is HTML/CSS/JS rendered in a native window by pywebview
(WebView2 on Windows, WKWebView on macOS). Source of truth:

- `src/konspekt/web/static/styles.css` — tokens and components;
- `src/konspekt/web/static/app.js` — screens and behaviour;
- `src/konspekt/web/api.py` — the Python bridge.

The previous Tk interface (`app.py`, `ui_surfaces.py`) remains as a fallback when
WebView is unavailable, or with `--classic`.

**Key characteristics**

- Neutral cool-grey surfaces, one indigo accent for primary actions and focus.
- Light and dark themes, following the OS by default.
- One primary action per lecture row: «Подготовить», «Создать конспект» or «Читать».
- Long work never blocks the UI: progress lives in the row, the sidebar dock and toasts.
- Typography tuned for reading: 720px measure, 1.72 line height, optional serif.

## Colors

Tokens are CSS custom properties on `:root`; the dark theme overrides the same names
under `:root[data-theme="dark"]`. Never hard-code colours in components.

- **Accent** (`--accent`, `--accent-soft`): primary buttons, selected navigation,
  active tab, focus ring, progress bars, timestamps.
- **Success** green: «Конспект готов», «Помню», completed tasks.
- **Warning** amber: optional tools missing, limits close to the provider maximum.
- **Danger** red: errors, «Не помню», destructive menu items.
- **Mark** yellow: in-text search hits (`mark.find-hit`, current hit is orange).

Semantic colour is always accompanied by text or an icon.

## Typography

System UI font at 14px for interface text. Page titles use the display face at
26px/650. The reader uses `--reading-font` (sans by default, serif on request) at
`16.5px × --reading-scale` (85–140 %), line height 1.72, headings with tight tracking.
Timestamps and code use the mono stack.

## Layout

- Left sidebar (236px): brand, «Добавить лекцию», navigation (Библиотека, Повторение
  with a due-cards badge, Настройки) and the task dock at the bottom.
- Content pages are centred with a max width of 960px and 40px side padding.
- Reader: sticky top bar (back, title, tabs Конспект / Карточки / Расшифровка, tools,
  reading-progress line), a sticky table of contents on the left with scroll-spy,
  the article in the centre, and a bottom audio player when a timestamp is clicked.
- Breakpoints: below 1180px spacing tightens; below 1060px the table of contents hides.

## Components

- **Buttons** `.btn` with `-primary`, `-soft`, `-ghost`, `-danger`, sizes `-sm`/`-lg`.
- **Lecture row** `.lecture-row`: stage icon, title, meta (date, duration, reading
  time, cards learned), inline progress with «Остановить», one next action and a `⋯` menu.
- **Banners**: «Сегодня к повторению» and «Продолжить чтение» at the top of the library.
- **Modals**: add lecture (link or file, optional automatic preparation and
  generation), generate (provider cards, «что отправляется / что остаётся», manual
  web-chat handoff), rename, trash, confirmation.
- **Flashcards** `.flashcard`: question, «Показать ответ» (Space), «Не помню» (1) /
  «Помню» (2). Leitner boxes 1–5 with intervals 0/1/3/7/16 days (`study_progress.py`).
- **Toasts** bottom-right, with an optional action («Открыть», «Показать»).

## Motion

Short (120–180ms) fades and lifts for modals, menus and toasts; no decorative
animation. `prefers-reduced-motion` disables transitions and indeterminate progress
animation.

## Window chrome

The native frame stays OS-owned. On Windows 11 the DWM caption colour, caption text
and border are set to match the sidebar of the active theme (`window_chrome.py`);
high-contrast mode skips this. Minimise/maximise/close, Snap and keyboard window
commands are untouched.

## Do's and Don'ts

**Do** keep one primary action per context, label every state in words, keep
keyboard paths (Ctrl+K search, Ctrl+N add, Ctrl+F find, Space/1/2 in cards, Esc).

**Don't** load anything from the network into the UI (all assets are local; CSP
forbids it), add gradients or decorative illustrations, or replace the native caption
with custom window buttons.
