/* Konspekt desktop interface. Talks to Python through window.pywebview.api. */
"use strict";

(() => {
  // ================================================================ helpers
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function el(html) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  }

  function plural(n, forms) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
  }

  const countText = (n, forms) => `${n} ${plural(n, forms)}`;
  const CARD_FORMS = ["карточка", "карточки", "карточек"];
  const LECTURE_FORMS = ["лекция", "лекции", "лекций"];

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function fmtClock(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function fmtDuration(seconds) {
    const minutes = Math.round((Number(seconds) || 0) / 60);
    if (minutes < 1) return "";
    if (minutes < 60) return `${minutes} мин`;
    const h = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${h} ч ${rest} мин` : `${h} ч`;
  }

  function debounce(fn, ms) {
    let timer = 0;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  function highlight(text, query) {
    if (!query) return esc(text);
    const lower = text.toLocaleLowerCase("ru");
    const needle = query.toLocaleLowerCase("ru");
    let out = "";
    let index = 0;
    for (;;) {
      const found = lower.indexOf(needle, index);
      if (found < 0 || !needle) break;
      out += esc(text.slice(index, found));
      out += `<mark>${esc(text.slice(found, found + needle.length))}</mark>`;
      index = found + needle.length;
    }
    return out + esc(text.slice(index));
  }

  function isTyping(target) {
    const node = target || document.activeElement;
    if (!node) return false;
    const tag = node.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable;
  }

  // ================================================================== icons
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    book: '<path d="M5 19V5.5A2.5 2.5 0 0 1 7.5 3H19v14H7.2A2.2 2.2 0 0 0 5 19.2 2 2 0 0 0 7 21h12"/><path d="M9 7.5h6"/>',
    cards: '<rect x="3" y="7.5" width="13.5" height="13" rx="2.2"/><path d="M7.5 4h11A2.5 2.5 0 0 1 21 6.5V17"/>',
    settings: '<path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1"/><circle cx="15" cy="6.5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17.5" r="2"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
    play: '<path d="M8 5.6v12.8a1 1 0 0 0 1.53.85l10.2-6.4a1 1 0 0 0 0-1.7L9.53 4.75A1 1 0 0 0 8 5.6z"/>',
    pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/>',
    back10: '<path d="M4 12a8 8 0 1 0 2.6-5.9L4 8.5"/><path d="M4 4v4.5h4.5"/>',
    fwd10: '<path d="M20 12a8 8 0 1 1-2.6-5.9L20 8.5"/><path d="M20 4v4.5h-4.5"/>',
    arrowLeft: '<path d="M19 12H5M11 18l-6-6 6-6"/>',
    more: '<circle cx="5.5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/>',
    trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6.5 7l.9 11.6a2 2 0 0 0 2 1.9h5.2a2 2 0 0 0 2-1.9L17.5 7M9.5 7V4.5h5V7"/>',
    edit: '<path d="M4 20h4L19 9a2.83 2.83 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    wave: '<path d="M4 10v4M8 6.5v11M12 4v16M16 8v8M20 10.5v3"/>',
    sparkles: '<path d="M11 4.5 12.6 9a2 2 0 0 0 1.4 1.4l4.5 1.6-4.5 1.6a2 2 0 0 0-1.4 1.4L11 19.5 9.4 15A2 2 0 0 0 8 13.6L3.5 12 8 10.4A2 2 0 0 0 9.4 9z"/><path d="M18.5 3v4M16.5 5h4"/>',
    refresh: '<path d="M19.5 11A7.5 7.5 0 0 0 6.2 6.6L4.5 8.5"/><path d="M4.5 4.5v4h4"/><path d="M4.5 13a7.5 7.5 0 0 0 13.3 4.4l1.7-1.9"/><path d="M19.5 19.5v-4h-4"/>',
    chevronRight: '<path d="m9.5 6 6 6-6 6"/>',
    chevronDown: '<path d="m6 9.5 6 6 6-6"/>',
    chevronUp: '<path d="m6 14.5 6-6 6 6"/>',
    external: '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
    lines: '<path d="M5 6.5h14M5 10.5h14M5 14.5h9M5 18.5h6"/>',
    alert: '<path d="M10.3 4.7 2.9 17.5a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.7a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    shield: '<path d="M12 3 5 6v5.2c0 4.3 2.9 8.2 7 9.8 4.1-1.6 7-5.5 7-9.8V6z"/><path d="m9 12 2 2 4-4"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 9.5h5v5h-5zM9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    theme: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" stroke="none"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16 7l2.5 2.5M13.8 9.2l2 2"/>',
    activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    image: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 8.5"/>',
    archive: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/>',
    text: '<path d="M4 18 8.5 6h1L14 18M5.7 14h6.6"/><path d="M15.5 18l2.8-7.5h.4L21.5 18M16.4 15.7h4.2"/>',
    flip: '<path d="M4 9a8 8 0 0 1 14-3.5L20 8"/><path d="M20 4v4h-4"/><path d="M20 15a8 8 0 0 1-14 3.5L4 16"/><path d="M4 20v-4h4"/>',
    target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8" fill="currentColor"/>',
    question: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6M12 17h.01"/>',
    chat: '<path d="M7.5 18.5 4 20.5V7a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 7v9a2.5 2.5 0 0 1-2.5 2.5z"/><path d="M8.5 9.5h7M8.5 13.5h4.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  };

  function icon(name, extra = "") {
    return `<svg class="icon ${extra}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  }

  // ==================================================================== api
  async function api(name, ...args) {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge[name] !== "function") {
      throw new Error("Нет связи с приложением. Перезапусти Конспект.");
    }
    const result = await bridge[name](...args);
    if (!result || result.ok !== true) {
      throw new Error((result && result.error) || "Неизвестная ошибка.");
    }
    return result.data;
  }

  async function attempt(promise, fallback = undefined) {
    try {
      return await promise;
    } catch (error) {
      toast(error.message || String(error), { type: "error" });
      return fallback;
    }
  }

  // ================================================================== state
  const S = {
    lectures: [],
    tasks: [],
    tasksVersion: -1,
    libraryVersion: -1,
    prefs: { theme: "system", reading_font: "sans", font_scale: 1 },
    settings: null,
    platform: "",
    version: "",
    filter: "all",
    query: "",
    searchResults: null,
    route: { name: "library" },
    chatMeta: null,
    chatgpt: null,
    knownTaskStatus: new Map(),
    tasksSeeded: false,
  };

  const lectureByKey = (key) => S.lectures.find((item) => item.key === key) || null;

  function tasksFor(key) {
    return S.tasks.filter((task) => task.lecture_key === key);
  }

  function activeTask(key) {
    const tasks = tasksFor(key).filter((task) => task.status === "running" || task.status === "queued");
    tasks.sort((a, b) => (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1));
    return tasks[0] || null;
  }

  function failedTask(key) {
    const failed = tasksFor(key).filter((task) => task.status === "failed");
    return failed[failed.length - 1] || null;
  }

  function totalDue() {
    return S.lectures.reduce((sum, item) => sum + (item.cards ? item.cards.due : 0), 0);
  }

  // ========================================================== theme & prefs
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  let appliedDark = null;

  function applyPreferences() {
    const prefs = S.prefs;
    const dark = prefs.theme === "dark" || (prefs.theme === "system" && darkQuery.matches);
    const root = document.documentElement;
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.readingFont = prefs.reading_font === "serif" ? "serif" : "sans";
    root.style.setProperty("--reading-scale", String(prefs.font_scale || 1));
    if (appliedDark !== dark) {
      appliedDark = dark;
      api("apply_window_theme", dark).catch(() => {});
    }
  }

  darkQuery.addEventListener("change", () => applyPreferences());

  const persistPreferences = debounce((changes) => {
    api("set_preferences", changes).catch(() => {});
  }, 400);

  function setPreference(name, value) {
    S.prefs = { ...S.prefs, [name]: value };
    applyPreferences();
    persistPreferences({ [name]: value });
  }

  // ================================================================= toasts
  function toast(message, options = {}) {
    const { type = "info", action = null, timeout = type === "error" ? 7000 : 4200 } = options;
    const iconName = type === "error" ? "alert" : type === "success" ? "check" : "info";
    const node = el(`<div class="toast ${type}" role="status">
      ${icon(iconName)}
      <div class="toast-body">${esc(message)}</div>
      ${action ? `<button type="button">${esc(action.label)}</button>` : ""}
    </div>`);
    const remove = () => node.remove();
    if (action) {
      $("button", node).addEventListener("click", () => {
        remove();
        action.run();
      });
    }
    $("#toasts").append(node);
    while ($("#toasts").children.length > 4) $("#toasts").firstElementChild.remove();
    setTimeout(remove, timeout);
  }

  // ================================================================== menus
  let openMenuNode = null;

  function closeMenu() {
    if (openMenuNode) {
      openMenuNode.remove();
      openMenuNode = null;
    }
  }

  function openMenu(anchor, items) {
    closeMenu();
    const menu = el('<div class="menu" role="menu"></div>');
    for (const item of items) {
      if (!item) continue;
      if (item === "-") {
        menu.append(el("<hr>"));
        continue;
      }
      if (item.heading) {
        menu.append(el(`<div class="menu-label">${esc(item.heading)}</div>`));
        continue;
      }
      const button = el(
        `<button type="button" role="menuitem" class="${item.danger ? "danger" : ""}">
          ${icon(item.icon || "chevronRight")}<span>${esc(item.label)}</span></button>`,
      );
      button.addEventListener("click", () => {
        closeMenu();
        item.run();
      });
      menu.append(button);
    }
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    let left = rect.right - width;
    if (left < 8) left = rect.left;
    let top = rect.bottom + 4;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
    menu.style.left = `${clamp(left, 8, window.innerWidth - width - 8)}px`;
    menu.style.top = `${top}px`;
    openMenuNode = menu;
    const first = $("button", menu);
    if (first) first.focus();
  }

  document.addEventListener("mousedown", (event) => {
    if (openMenuNode && !openMenuNode.contains(event.target)) closeMenu();
  });
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);

  // ================================================================= modals
  const modalStack = [];

  function openModal(html, { size = "", onClose = null, dismissible = true } = {}) {
    const backdrop = el(`<div class="modal-backdrop"><div class="modal ${size}" role="dialog" aria-modal="true">${html}</div></div>`);
    const modal = $(".modal", backdrop);
    const entry = { backdrop, modal, onClose, dismissible };
    const close = () => {
      const index = modalStack.indexOf(entry);
      if (index >= 0) modalStack.splice(index, 1);
      backdrop.remove();
      if (onClose) onClose();
    };
    entry.close = close;
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop && entry.dismissible) close();
    });
    $$("[data-close]", modal).forEach((button) => button.addEventListener("click", close));
    $("#modal-root").append(backdrop);
    modalStack.push(entry);
    const focusTarget = $("[autofocus]", modal) || $("input, textarea, select, button.btn-primary", modal);
    if (focusTarget) setTimeout(() => focusTarget.focus(), 30);
    return entry;
  }

  function closeTopModal() {
    const top = modalStack[modalStack.length - 1];
    if (top && top.dismissible) top.close();
    return Boolean(top);
  }

  function modalHead(title, subtitle = "") {
    return `<div class="modal-head">
      <h2 class="modal-title">${esc(title)}${subtitle ? `<p class="modal-sub">${esc(subtitle)}</p>` : ""}</h2>
      <button type="button" class="icon-btn" data-close aria-label="Закрыть">${icon("x")}</button>
    </div>`;
  }

  function confirmDialog({ title, text, confirm = "Подтвердить", danger = false }) {
    return new Promise((resolve) => {
      let answered = false;
      const entry = openModal(
        `${modalHead(title)}
        <div class="modal-body"><p class="muted" style="margin:0">${esc(text)}</p></div>
        <div class="modal-foot">
          <button type="button" class="btn" data-close>Отмена</button>
          <button type="button" class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok>${esc(confirm)}</button>
        </div>`,
        {
          size: "modal-small",
          onClose: () => {
            if (!answered) resolve(false);
          },
        },
      );
      $("[data-ok]", entry.modal).addEventListener("click", () => {
        answered = true;
        entry.close();
        resolve(true);
      });
      setTimeout(() => $("[data-ok]", entry.modal).focus(), 40);
    });
  }

  function setBusy(button, busy, label = null) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="spinner"></span>${label ? esc(label) : button.textContent}`;
    } else {
      button.disabled = false;
      if (button.dataset.label) button.innerHTML = button.dataset.label;
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      const area = el('<textarea style="position:fixed;left:-9999px;top:0"></textarea>');
      area.value = text;
      document.body.append(area);
      area.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (inner) {
        ok = false;
      }
      area.remove();
      return ok;
    }
  }

  // ================================================================ sidebar
  function renderNav() {
    const due = totalDue();
    const route = S.route.name;
    const active = (name) => (route === name || (name === "library" && route === "lecture") ? "active" : "");
    $("#nav").innerHTML = `
      <button type="button" class="nav-item ${active("library")}" data-go="#/library">
        ${icon("book")}<span>Библиотека</span><span class="nav-count">${S.lectures.length || ""}</span>
      </button>
      <button type="button" class="nav-item ${active("review")}" data-go="#/review">
        ${icon("cards")}<span>Повторение</span>${due ? `<span class="nav-badge">${due > 999 ? "999+" : due}</span>` : ""}
      </button>
      <button type="button" class="nav-item ${active("chat")}" data-go="#/chat">
        ${icon("chat")}<span>Чат</span>
      </button>
      <button type="button" class="nav-item ${active("settings")}" data-go="#/settings">
        ${icon("settings")}<span>Настройки</span>
      </button>`;
  }

  const TASK_LABEL = { prepare: "Подготовка", generate: "Создание конспекта" };

  function renderDock() {
    const dock = $("#dock");
    const visible = S.tasks.filter((task) => task.status !== "cancelled");
    dock.innerHTML = visible
      .slice(-4)
      .map((task) => {
        const working = task.status === "running" || task.status === "queued";
        const percent = task.status === "running" ? task.percent : 0;
        let body = "";
        if (working) {
          body = `<div class="dock-msg" title="${esc(task.message)}">${esc(task.cancelling ? "Останавливаем…" : task.message)}</div>
            <div class="progress ${task.status === "queued" ? "" : percent < 2 ? "indeterminate" : ""}">
              <div class="progress-bar" style="width:${percent}%"></div></div>`;
        } else if (task.status === "failed") {
          body = `<div class="dock-msg">${esc(task.error || "Ошибка")}</div>`;
        } else {
          body = `<div class="dock-msg">${esc(task.result || "Готово")}</div>`;
        }
        const lead = working
          ? task.status === "queued"
            ? `<span class="dock-kind">В очереди</span>`
            : '<span class="spinner"></span>'
          : task.status === "failed"
            ? `<span class="status-dot bad">${icon("x")}</span>`
            : `<span class="status-dot ok">${icon("check")}</span>`;
        const actionButton = working
          ? `<button type="button" class="icon-btn" data-task-cancel="${esc(task.id)}" title="Остановить" aria-label="Остановить">${icon("x")}</button>`
          : `<button type="button" class="icon-btn" data-task-dismiss="${esc(task.id)}" title="Скрыть" aria-label="Скрыть">${icon("x")}</button>`;
        return `<div class="dock-item ${task.status}" data-task-open="${esc(task.lecture_key)}">
          <div class="dock-head">${lead}<span class="dock-title" title="${esc(task.title)}">${esc(TASK_LABEL[task.kind] || "")}: ${esc(task.title)}</span>${actionButton}</div>
          ${body}</div>`;
      })
      .join("");
  }

  document.addEventListener("click", (event) => {
    // Links inside notes open in the system browser, never inside the app window.
    const link = event.target.closest("a.ext[href]");
    if (link) {
      event.preventDefault();
      const href = link.getAttribute("href");
      if (/^https?:\/\//i.test(href)) attempt(api("open_external", href));
      else if (/^mailto:/i.test(href)) {
        const address = decodeURIComponent(href.slice(7).split("?")[0]);
        copyText(address).then((ok) => toast(ok ? `Адрес ${address} скопирован` : address));
      }
      return;
    }
    const go = event.target.closest("[data-go]");
    if (go) {
      location.hash = go.dataset.go;
      return;
    }
    const cancel = event.target.closest("[data-task-cancel]");
    if (cancel) {
      event.stopPropagation();
      attempt(api("cancel_task", cancel.dataset.taskCancel));
      return;
    }
    const dismiss = event.target.closest("[data-task-dismiss]");
    if (dismiss) {
      event.stopPropagation();
      dismissTask(dismiss.dataset.taskDismiss);
      return;
    }
    const open = event.target.closest("[data-task-open]");
    if (open && lectureByKey(open.dataset.taskOpen)) {
      location.hash = `#/lecture/${open.dataset.taskOpen}`;
    }
  });

  function dismissTask(taskId) {
    S.tasks = S.tasks.filter((task) => task.id !== taskId);
    renderDock();
    refreshCurrentView("tasks");
    api("dismiss_task", taskId).catch(() => {});
  }

  // ================================================================ polling
  function applyTasks(tasks) {
    const previous = S.knownTaskStatus;
    const next = new Map();
    for (const task of tasks) {
      next.set(task.id, task.status);
      const before = previous.get(task.id);
      // A task first seen after start-up may have finished between two polls.
      const wasActive = before === undefined ? S.tasksSeeded : before === "running" || before === "queued";
      if (!wasActive || task.status === before) continue;
      if (task.status === "done") onTaskDone(task);
      if (task.status === "failed") {
        toast(`${TASK_LABEL[task.kind] || "Задача"}: ${task.error || "ошибка"}`, { type: "error", timeout: 9000 });
      }
    }
    // Keep statuses of dismissed tasks so a late poll does not toast them again.
    for (const [id, status] of previous) if (!next.has(id)) next.set(id, status);
    S.knownTaskStatus = next;
    S.tasksSeeded = true;
    S.tasks = tasks;
  }

  function onTaskDone(task) {
    if (task.kind === "generate") {
      toast(`Конспект готов: «${task.title}»`, {
        type: "success",
        timeout: 9000,
        action: { label: "Открыть", run: () => (location.hash = `#/lecture/${task.lecture_key}`) },
      });
    } else if (task.kind === "prepare") {
      const followUp = S.tasks.some((other) => other.kind === "generate" && other.lecture_key === task.lecture_key);
      toast(`Лекция подготовлена: «${task.title}»`, {
        type: "success",
        action: followUp
          ? null
          : { label: "Создать конспект", run: () => openGenerateModal(task.lecture_key) },
      });
    }
    // Finished tasks leave the dock by themselves; failures stay until dismissed.
    setTimeout(() => {
      if (S.tasks.some((item) => item.id === task.id && item.status === "done")) dismissTask(task.id);
    }, 6000);
  }

  async function pollLoop() {
    for (;;) {
      try {
        const result = await api("poll", S.tasksVersion, S.libraryVersion);
        S.tasksVersion = result.version;
        applyTasks(result.tasks || []);
        let libraryChanged = false;
        if (result.lectures) {
          S.lectures = result.lectures;
          S.libraryVersion = result.library_version;
          libraryChanged = true;
        }
        renderDock();
        renderNav();
        refreshCurrentView(libraryChanged ? "library" : "tasks");
        await sleep(250);
      } catch (error) {
        await sleep(2000);
      }
    }
  }

  async function reloadLectures() {
    try {
      S.lectures = await api("list_lectures");
      renderNav();
      refreshCurrentView("library");
    } catch (error) {
      toast(error.message, { type: "error" });
    }
  }

  // ================================================================= router
  let currentView = null;

  function parseRoute() {
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if (parts[0] === "lecture" && parts[1]) return { name: "lecture", key: parts[1], tab: parts[2] || "" };
    if (parts[0] === "review") return { name: "review" };
    if (parts[0] === "chat") return { name: "chat", id: parts[1] || "" };
    if (parts[0] === "settings") return { name: "settings" };
    return { name: "library" };
  }

  function route() {
    const next = parseRoute();
    // A view may handle a change inside itself, e.g. switching between chats.
    if (currentView && currentView.update && S.route.name === next.name && currentView.update(next)) {
      closeMenu();
      S.route = next;
      renderNav();
      return;
    }
    if (currentView && currentView.leave) currentView.leave();
    closeMenu();
    S.route = next;
    renderNav();
    const view = $("#view");
    view.innerHTML = "";
    if (next.name === "lecture") currentView = LectureView(view, next.key, next.tab);
    else if (next.name === "review") currentView = ReviewView(view);
    else if (next.name === "chat") currentView = ChatView(view, next.id);
    else if (next.name === "settings") currentView = SettingsView(view);
    else currentView = LibraryView(view);
  }

  function refreshCurrentView(reason) {
    if (currentView && currentView.refresh) currentView.refresh(reason);
  }

  window.addEventListener("hashchange", route);

  // ================================================================ library
  const FILTERS = [
    { id: "all", label: "Все" },
    { id: "ready", label: "Конспект готов" },
    { id: "todo", label: "Ждут действия" },
    { id: "working", label: "В работе" },
  ];

  function lectureStatus(lecture) {
    const task = activeTask(lecture.key);
    if (task) {
      return {
        kind: "working",
        label: task.status === "queued" ? "В очереди" : TASK_LABEL[task.kind],
        task,
      };
    }
    const failed = failedTask(lecture.key);
    if (failed) return { kind: "failed", label: "Ошибка", task: failed };
    if (lecture.stage === "lesson") return { kind: "ready", label: "Конспект готов" };
    if (lecture.stage === "package" || lecture.stage === "prepared") {
      return { kind: "next", label: "Можно создавать конспект" };
    }
    if (lecture.stage === "partial") return { kind: "todo", label: "Подготовка прервана" };
    return { kind: "todo", label: "Не подготовлена" };
  }

  function matchesFilter(lecture, filter) {
    const kind = lectureStatus(lecture).kind;
    if (filter === "ready") return kind === "ready";
    if (filter === "working") return kind === "working";
    if (filter === "todo") return kind === "todo" || kind === "next" || kind === "failed";
    return true;
  }

  function lectureMeta(lecture) {
    const parts = [`<span>${icon("clock")}${esc(lecture.imported_label)}</span>`];
    const duration = fmtDuration(lecture.duration);
    if (duration) parts.push(`<span>${icon("wave")}${esc(duration)}</span>`);
    if (lecture.stage === "lesson" && lecture.reading_minutes) {
      parts.push(`<span>${icon("book")}${lecture.reading_minutes} мин чтения</span>`);
    }
    if (lecture.cards && lecture.cards.total) {
      const known = lecture.cards.known;
      const share = Math.round((known / lecture.cards.total) * 100);
      parts.push(
        `<span title="Выучено карточек: ${known} из ${lecture.cards.total}">${icon("cards")}${known}/${lecture.cards.total}
        <span class="mini-progress"><i style="width:${share}%"></i></span></span>`,
      );
    }
    if (lecture.source === "local") parts.push(`<span>${icon("file")}Файл</span>`);
    return parts.join("");
  }

  function lectureRow(lecture) {
    const status = lectureStatus(lecture);
    const iconName = { ready: "book", next: "sparkles", todo: "wave", failed: "alert" }[status.kind];
    const lead =
      status.kind === "working"
        ? '<div class="lecture-icon working"><span class="spinner spinner-lg"></span></div>'
        : `<div class="lecture-icon ${status.kind}">${icon(iconName, "icon-lg")}</div>`;
    let detail = "";
    let actions = "";
    if (status.kind === "working") {
      const task = status.task;
      const percent = task.status === "running" ? task.percent : 0;
      detail = `<div class="lecture-progress">
        <div class="progress ${task.status === "running" && percent < 2 ? "indeterminate" : ""}"><div class="progress-bar" style="width:${percent}%"></div></div>
        <span class="progress-text">${esc(status.label)} · ${task.status === "running" ? `${percent}% · ` : ""}${esc(task.cancelling ? "Останавливаем…" : task.message)}</span></div>`;
      actions = `<button type="button" class="btn btn-sm" data-act="cancel" data-task="${esc(task.id)}">Остановить</button>`;
    } else if (status.kind === "failed") {
      detail = `<div class="lecture-error">${esc(status.task.error)}</div>`;
      actions = `<button type="button" class="btn btn-sm btn-primary" data-act="retry">${icon("refresh", "icon-sm")}Повторить</button>`;
    } else if (status.kind === "ready") {
      actions = `<button type="button" class="btn btn-sm btn-soft" data-act="read">${icon("book", "icon-sm")}Читать</button>`;
    } else if (status.kind === "next") {
      actions = `<button type="button" class="btn btn-sm btn-primary" data-act="generate">${icon("sparkles", "icon-sm")}Создать конспект</button>`;
    } else {
      actions = `<button type="button" class="btn btn-sm btn-primary" data-act="prepare" title="Расшифровка речи и текста со слайдов на этом компьютере">${icon("cpu", "icon-sm")}Подготовить</button>`;
    }
    return `<div class="lecture-row clickable" data-key="${esc(lecture.key)}">
      ${lead}
      <div class="lecture-main">
        <div class="lecture-title" title="${esc(lecture.title)}">${esc(lecture.title)}</div>
        <div class="lecture-meta">${lectureMeta(lecture)}</div>
        ${detail}
      </div>
      <div class="lecture-actions">${actions}
        <button type="button" class="icon-btn" data-act="menu" aria-label="Ещё">${icon("more")}</button>
      </div>
    </div>`;
  }

  function lectureMenuItems(lecture, { inReader = false } = {}) {
    const key = lecture.key;
    const working = Boolean(activeTask(key));
    const items = [];
    if (!inReader) items.push({ label: "Открыть", icon: "book", run: () => (location.hash = `#/lecture/${key}`) });
    if (!working) {
      if (lecture.stage === "lesson") {
        items.push({ label: "Создать конспект заново", icon: "sparkles", run: () => openGenerateModal(key) });
      } else if (lecture.stage === "package" || lecture.stage === "prepared") {
        items.push({ label: "Создать конспект", icon: "sparkles", run: () => openGenerateModal(key) });
      } else {
        items.push({ label: "Подготовить", icon: "cpu", run: () => startPrepare(key) });
      }
    }
    if (lecture.stage === "lesson") {
      items.push("-", { heading: "Экспорт конспекта" });
      items.push({ label: "PDF", icon: "download", run: () => exportLesson(key, "pdf") });
      items.push({ label: "Веб-страница (HTML)", icon: "download", run: () => exportLesson(key, "html") });
      items.push({ label: "Markdown", icon: "download", run: () => exportLesson(key, "md") });
    }
    items.push("-");
    items.push({ label: "Архив лекции (ZIP)", icon: "archive", run: () => exportLesson(key, "zip") });
    items.push({ label: "Открыть папку лекции", icon: "folder", run: () => attempt(api("open_folder", key)) });
    items.push({ label: "Переименовать", icon: "edit", run: () => openRenameModal(lecture) });
    items.push("-");
    items.push({ label: "Удалить в корзину", icon: "trash", danger: true, run: () => trashLecture(lecture) });
    return items;
  }

  async function startPrepare(key, thenGenerate = null) {
    const task = await attempt(api("prepare", key, thenGenerate));
    if (task) {
      S.tasks = [...S.tasks.filter((item) => item.id !== task.id), task];
      renderDock();
      refreshCurrentView("tasks");
    }
  }

  async function exportLesson(key, format) {
    const result = await attempt(api("export_lesson", key, format));
    if (result && result.path) {
      toast("Файл сохранён", {
        type: "success",
        action: { label: "Показать", run: () => attempt(api("reveal_path", result.path)) },
      });
    }
  }

  async function trashLecture(lecture) {
    const ok = await confirmDialog({
      title: "Удалить лекцию в корзину?",
      text: `«${lecture.title}» и все её файлы будут перемещены в корзину. Восстановить можно из библиотеки → Корзина.`,
      confirm: "В корзину",
      danger: true,
    });
    if (!ok) return;
    const done = await attempt(api("trash", lecture.key), false);
    if (done !== false) {
      toast("Лекция перемещена в корзину", { action: { label: "Корзина", run: openTrashModal } });
      if (S.route.name === "lecture") location.hash = "#/library";
      reloadLectures();
    }
  }

  function retryFailed(lecture) {
    const failed = failedTask(lecture.key);
    if (failed) dismissTask(failed.id);
    if (failed && failed.kind === "generate") openGenerateModal(lecture.key);
    else startPrepare(lecture.key);
  }

  function LibraryView(root) {
    root.innerHTML = `<div class="page" id="library-page"><div class="page-inner">
      <div class="page-head">
        <div><h1 class="page-title">Библиотека</h1><p class="page-sub" id="library-sub"></p></div>
      </div>
      <div class="toolbar">
        <div class="search-box">${icon("search")}
          <input class="input" id="library-search" type="search" placeholder="Поиск по названиям и тексту конспектов" autocomplete="off" value="${esc(S.query)}">
          <kbd>Ctrl K</kbd>
        </div>
      </div>
      <div class="chips" id="library-chips" style="margin-bottom:18px"></div>
      <div id="library-top"></div>
      <div id="library-results"></div>
      <div id="library-list"></div>
      <div class="library-footer" id="library-footer"></div>
    </div></div>`;

    const search = $("#library-search", root);
    const runSearch = debounce(async () => {
      const query = S.query.trim();
      if (query.length < 2) {
        S.searchResults = null;
        renderResults();
        return;
      }
      try {
        const results = await api("search", query);
        if (S.query.trim() === query) {
          S.searchResults = results.filter((item) => item.snippets.length);
          renderResults();
        }
      } catch (error) {
        S.searchResults = null;
      }
    }, 220);

    search.addEventListener("input", () => {
      S.query = search.value;
      renderList();
      runSearch();
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && search.value) {
        event.stopPropagation();
        search.value = "";
        S.query = "";
        S.searchResults = null;
        renderList();
        renderResults();
      }
    });

    // #view outlives this page, so listen on the page itself.
    $("#library-page", root).addEventListener("click", (event) => {
      const chip = event.target.closest("[data-filter]");
      if (chip) {
        S.filter = chip.dataset.filter;
        renderList();
        return;
      }
      const hit = event.target.closest("[data-hit]");
      if (hit) {
        location.hash = `#/lecture/${hit.dataset.hit}`;
        pendingFind = S.query.trim();
        return;
      }
      const top = event.target.closest("[data-top]");
      if (top) {
        if (top.dataset.top === "review") location.hash = "#/review";
        else location.hash = `#/lecture/${top.dataset.top}`;
        return;
      }
      const footer = event.target.closest("[data-footer]");
      if (footer) {
        if (footer.dataset.footer === "trash") openTrashModal();
        if (footer.dataset.footer === "folder") attempt(api("open_data_folder"));
        return;
      }
      const empty = event.target.closest("[data-empty]");
      if (empty) {
        openAddModal(empty.dataset.empty);
        return;
      }
      const row = event.target.closest(".lecture-row");
      if (!row) return;
      const lecture = lectureByKey(row.dataset.key);
      if (!lecture) return;
      const action = event.target.closest("[data-act]");
      if (!action) {
        location.hash = `#/lecture/${lecture.key}`;
        return;
      }
      event.stopPropagation();
      const act = action.dataset.act;
      if (act === "menu") openMenu(action, lectureMenuItems(lecture));
      else if (act === "cancel") attempt(api("cancel_task", action.dataset.task));
      else if (act === "retry") retryFailed(lecture);
      else if (act === "read") location.hash = `#/lecture/${lecture.key}`;
      else if (act === "generate") openGenerateModal(lecture.key);
      else if (act === "prepare") startPrepare(lecture.key);
    });

    function renderTop() {
      const top = $("#library-top", root);
      if (S.query.trim()) {
        top.innerHTML = "";
        return;
      }
      let html = "";
      const due = totalDue();
      if (due) {
        const lectures = S.lectures.filter((item) => item.cards && item.cards.due).length;
        html += `<div class="banner">
          <div class="banner-icon">${icon("cards", "icon-lg")}</div>
          <div class="banner-body"><div class="banner-title">Сегодня к повторению: ${countText(due, CARD_FORMS)}</div>
          <div class="banner-text">Из ${countText(lectures, LECTURE_FORMS)}. Пара минут в день — и материал не забудется к экзамену.</div></div>
          <button type="button" class="btn btn-primary" data-top="review">Повторить</button></div>`;
      }
      const reading = S.lectures
        .filter((item) => item.stage === "lesson" && item.last_opened && item.reading_position > 0.02 && item.reading_position < 0.97)
        .sort((a, b) => (a.last_opened < b.last_opened ? 1 : -1))[0];
      if (reading) {
        const percent = Math.round(reading.reading_position * 100);
        html += `<div class="continue-card" data-top="${esc(reading.key)}">
          <div class="banner-body">
            <div class="continue-eyebrow">Продолжить чтение</div>
            <div class="continue-title">${esc(reading.title)}</div>
            <div class="continue-meta">Прочитано ${percent}% · осталось примерно ${Math.max(1, Math.round(reading.reading_minutes * (1 - reading.reading_position)))} мин</div>
            <div class="progress" style="margin-top:10px;max-width:320px"><div class="progress-bar" style="width:${percent}%"></div></div>
          </div>
          <button type="button" class="btn btn-soft">${icon("book", "icon-sm")}Продолжить</button>
        </div>`;
      }
      top.innerHTML = html;
    }

    function renderResults() {
      const box = $("#library-results", root);
      const results = S.searchResults;
      if (!results || !results.length || !S.query.trim()) {
        box.innerHTML = "";
        return;
      }
      const query = S.query.trim();
      box.innerHTML = `<div class="section-title" style="margin-top:0">Найдено в конспектах</div>
        <div class="lecture-list search-results">${results
          .map(
            (item) => `<button type="button" class="search-hit" data-hit="${esc(item.key)}">
              <div class="search-hit-title">${esc(item.title)}</div>
              ${item.snippets.map((snippet) => `<div class="search-hit-snippet">${highlight(snippet, query)}</div>`).join("")}
            </button>`,
          )
          .join("")}</div>
        <div class="section-title">Лекции</div>`;
    }

    function renderList() {
      const counts = Object.fromEntries(FILTERS.map((f) => [f.id, S.lectures.filter((l) => matchesFilter(l, f.id)).length]));
      $("#library-chips", root).innerHTML = FILTERS.filter((f) => f.id === "all" || counts[f.id] || S.filter === f.id)
        .map(
          (f) => `<button type="button" class="chip ${S.filter === f.id ? "active" : ""}" data-filter="${f.id}">
            ${esc(f.label)}<span class="chip-count">${counts[f.id]}</span></button>`,
        )
        .join("");
      const ready = S.lectures.filter((item) => item.stage === "lesson").length;
      $("#library-sub", root).textContent = S.lectures.length
        ? `${countText(S.lectures.length, LECTURE_FORMS)} · конспектов готово: ${ready}`
        : "Здесь будут твои лекции и конспекты";

      const list = $("#library-list", root);
      if (!S.lectures.length) {
        $("#library-chips", root).innerHTML = "";
        $(".toolbar", root).style.display = "none";
        list.innerHTML = emptyLibrary();
        $("#library-footer", root).innerHTML = "";
        return;
      }
      $(".toolbar", root).style.display = "";
      const query = S.query.trim().toLocaleLowerCase("ru");
      const searchKeys = new Set((S.searchResults || []).map((item) => item.key));
      const items = S.lectures.filter(
        (item) =>
          matchesFilter(item, S.filter) &&
          (!query || item.title.toLocaleLowerCase("ru").includes(query) || searchKeys.has(item.key)),
      );
      list.innerHTML = items.length
        ? `<div class="lecture-list">${items.map(lectureRow).join("")}</div>`
        : `<div class="empty"><p class="empty-text">Ничего не найдено. Попробуй другой запрос или фильтр.</p></div>`;
      $("#library-footer", root).innerHTML = `
        <button type="button" class="btn btn-ghost btn-sm" data-footer="folder">${icon("folder", "icon-sm")}Папка с данными</button>
        <button type="button" class="btn btn-ghost btn-sm" data-footer="trash">${icon("trash", "icon-sm")}Корзина</button>`;
      renderTop();
    }

    renderList();
    renderResults();
    if (S.query.trim().length >= 2) runSearch();

    return {
      refresh() {
        renderList();
      },
      focusSearch() {
        search.focus();
        search.select();
      },
    };
  }

  function emptyLibrary() {
    return `<div class="empty">
      <div class="empty-art">${icon("book")}</div>
      <h2 class="empty-title">Добавь первую лекцию</h2>
      <p class="empty-text">Вставь ссылку на запись BigBlueButton или выбери аудио/видео файл. Конспект сделает из лекции понятный текст с вопросами для самопроверки и карточками.</p>
      <div class="empty-actions">
        <button type="button" class="btn btn-primary btn-lg" data-empty="link">${icon("link")}Вставить ссылку</button>
        <button type="button" class="btn btn-lg" data-empty="file">${icon("file")}Выбрать файл</button>
      </div>
      <div class="steps">
        <div class="step"><div class="step-num">1</div><div class="step-title">Подготовка</div>
          <div class="step-text">Речь расшифровывается, слайды распознаются — всё на этом компьютере.</div></div>
        <div class="step"><div class="step-num">2</div><div class="step-title">Конспект</div>
          <div class="step-text">Текст лекции превращается в структурированный конспект через ChatGPT или API.</div></div>
        <div class="step"><div class="step-num">3</div><div class="step-title">Повторение</div>
          <div class="step-text">Читай с таймкодами на запись и закрепляй материал карточками.</div></div>
      </div>
    </div>`;
  }

  let pendingFind = "";

  // ================================================================ add lecture
  function openAddModal(initialMode = "link") {
    let mode = initialMode === "file" ? "file" : "link";
    let filePath = "";
    const canChatGPT = Boolean(S.chatgpt && S.chatgpt.signed_in);
    const apiReady = S.settings && S.settings.api_configured;
    const generators = [
      canChatGPT ? { id: "chatgpt", label: "ChatGPT" } : null,
      apiReady ? { id: "api", label: `API ${S.settings.provider_label}` } : null,
    ].filter(Boolean);

    const entry = openModal(
      `${modalHead("Добавить лекцию", "Запись из BigBlueButton или файл с компьютера")}
      <div class="modal-body">
        <div class="segmented source-tabs" role="tablist">
          <button type="button" data-mode="link">${icon("link", "icon-sm")} По ссылке</button>
          <button type="button" data-mode="file">${icon("file", "icon-sm")} Из файла</button>
        </div>
        <div data-pane="link">
          <div class="field">
            <label class="field-label" for="add-url">Ссылка на запись</label>
            <input class="input input-lg" id="add-url" type="url" placeholder="https://…/playback/presentation/2.3/…" autocomplete="off" spellcheck="false">
            <span class="field-hint">Скопируй адрес страницы с записью лекции из браузера.</span>
          </div>
        </div>
        <div data-pane="file" hidden>
          <div class="field">
            <button type="button" class="file-pick" id="add-file">
              <span class="banner-icon">${icon("file")}</span>
              <span><span class="file-name" id="add-file-name">Выбрать аудио или видео…</span>
              <span class="field-hint" style="display:block">MP4, MP3, M4A, WAV, MKV, WEBM, AAC</span></span>
            </button>
          </div>
          <div class="field">
            <label class="field-label" for="add-title">Название <span class="faint">(необязательно)</span></label>
            <input class="input" id="add-title" type="text" placeholder="Например: Базы данных — лекция 3">
          </div>
        </div>
        <div class="options">
          <label class="check"><input type="checkbox" id="add-prepare" checked>
            <span><span class="check-title">Сразу подготовить лекцию</span>
            <span class="check-hint">Расшифровка речи и текста со слайдов. Работает на этом компьютере, ничего не отправляет.</span></span></label>
          ${
            generators.length
              ? `<label class="check"><input type="checkbox" id="add-generate">
            <span><span class="check-title">Потом сразу создать конспект через
              <select class="select" id="add-generator">${generators.map((g) => `<option value="${g.id}">${esc(g.label)}</option>`).join("")}</select></span>
            <span class="check-hint">Текст расшифровки и слайдов будет отправлен выбранному сервису. Аудио, видео и ссылка на запись не отправляются.</span></span></label>`
              : ""
          }
        </div>
        <div id="add-error" class="notice notice-danger" style="margin-top:14px" hidden></div>
      </div>
      <div class="modal-foot">
        <button type="button" class="btn" data-close>Отмена</button>
        <button type="button" class="btn btn-primary" id="add-submit">${icon("plus", "icon-sm")}Добавить</button>
      </div>`,
    );
    const modal = entry.modal;

    function setMode(next) {
      mode = next;
      $$("[data-mode]", modal).forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
      $$("[data-pane]", modal).forEach((pane) => (pane.hidden = pane.dataset.pane !== mode));
      setTimeout(() => (mode === "link" ? $("#add-url", modal).focus() : $("#add-file", modal).focus()), 20);
    }

    $$("[data-mode]", modal).forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
    setMode(mode);

    const prepareBox = $("#add-prepare", modal);
    const generateBox = $("#add-generate", modal);
    if (generateBox) {
      prepareBox.addEventListener("change", () => {
        generateBox.disabled = !prepareBox.checked;
        if (!prepareBox.checked) generateBox.checked = false;
      });
    }

    async function pickFile() {
      const path = await attempt(api("choose_media_file"));
      if (path) {
        filePath = path;
        $("#add-file-name", modal).textContent = path.split(/[\\/]/).pop();
      }
    }

    $("#add-file", modal).addEventListener("click", pickFile);
    if (initialMode === "file") setTimeout(pickFile, 60);

    const showError = (message) => {
      const box = $("#add-error", modal);
      box.innerHTML = `${icon("alert")}<div>${esc(message)}</div>`;
      box.hidden = false;
    };

    $("#add-url", modal).addEventListener("keydown", (event) => {
      if (event.key === "Enter") $("#add-submit", modal).click();
    });

    $("#add-submit", modal).addEventListener("click", async (event) => {
      const button = event.currentTarget;
      $("#add-error", modal).hidden = true;
      const prepare = prepareBox.checked;
      const thenGenerate =
        generateBox && generateBox.checked ? { provider: $("#add-generator", modal).value, model: "" } : null;
      try {
        let lecture;
        if (mode === "link") {
          const url = $("#add-url", modal).value.trim();
          if (!url) {
            showError("Вставь ссылку на запись лекции.");
            return;
          }
          setBusy(button, true, "Проверяем ссылку…");
          entry.dismissible = false;
          lecture = await api("add_link", url, prepare, thenGenerate);
        } else {
          if (!filePath) {
            showError("Сначала выбери файл.");
            return;
          }
          setBusy(button, true, "Копируем файл…");
          entry.dismissible = false;
          lecture = await api("import_media", filePath, $("#add-title", modal).value.trim(), prepare, thenGenerate);
        }
        entry.dismissible = true;
        entry.close();
        toast(prepare ? `Лекция добавлена, начинаем подготовку` : "Лекция добавлена", { type: "success" });
        await reloadLectures();
        if (lecture && S.route.name !== "library") location.hash = "#/library";
      } catch (error) {
        entry.dismissible = true;
        setBusy(button, false);
        showError(error.message);
      }
    });
  }

  // ============================================================== rename
  function openRenameModal(lecture) {
    const entry = openModal(
      `${modalHead("Переименовать лекцию")}
      <div class="modal-body"><div class="field" style="margin:0">
        <input class="input input-lg" id="rename-input" type="text" maxlength="200" value="${esc(lecture.title)}" autofocus>
      </div></div>
      <div class="modal-foot">
        <button type="button" class="btn" data-close>Отмена</button>
        <button type="button" class="btn btn-primary" id="rename-save">Сохранить</button>
      </div>`,
      { size: "modal-small" },
    );
    const input = $("#rename-input", entry.modal);
    setTimeout(() => input.select(), 40);
    const save = async () => {
      const title = input.value.trim();
      if (!title) return;
      const result = await attempt(api("rename", lecture.key, title));
      if (result) {
        entry.close();
        reloadLectures();
        if (currentView && currentView.renamed) currentView.renamed(title);
      }
    };
    $("#rename-save", entry.modal).addEventListener("click", save);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") save();
    });
  }

  // =============================================================== trash
  async function openTrashModal() {
    const entry = openModal(
      `${modalHead("Корзина", "Удалённые лекции можно вернуть в библиотеку")}
      <div class="modal-body" id="trash-body"><div class="empty"><span class="spinner spinner-lg"></span></div></div>
      <div class="modal-foot"><span class="grow"></span>
        <button type="button" class="btn btn-ghost" id="trash-empty" hidden>${icon("trash", "icon-sm")}Очистить корзину</button>
        <button type="button" class="btn" data-close>Готово</button></div>`,
    );
    const body = $("#trash-body", entry.modal);
    async function load() {
      const items = await attempt(api("list_trash"), []);
      $("#trash-empty", entry.modal).hidden = !items.length;
      body.innerHTML = items.length
        ? items
            .map(
              (item, index) => `<div class="trash-item">
              <div class="grow"><div style="font-weight:600">${esc(item.title)}</div>
              <div class="faint small">Удалена ${esc(item.trashed_label)} · ${(item.size_bytes / 1048576).toFixed(1)} МБ</div></div>
              <button type="button" class="btn btn-sm" data-restore="${index}">${icon("refresh", "icon-sm")}Восстановить</button></div>`,
            )
            .join("")
        : `<div class="empty" style="padding:30px"><p class="empty-text" style="margin:0">Корзина пуста.</p></div>`;
      $$("[data-restore]", body).forEach((button) =>
        button.addEventListener("click", async () => {
          const item = items[Number(button.dataset.restore)];
          setBusy(button, true);
          const ok = await attempt(api("restore", item.meeting_id, item.source_url), false);
          if (ok !== false) {
            toast(`«${item.title}» восстановлена`, { type: "success" });
            reloadLectures();
          }
          load();
        }),
      );
    }
    $("#trash-empty", entry.modal).addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "Очистить корзину?",
        text: "Лекции из корзины будут удалены с компьютера навсегда. Это действие нельзя отменить.",
        confirm: "Удалить навсегда",
        danger: true,
      });
      if (!ok) return;
      const count = await attempt(api("empty_trash"));
      if (count !== undefined) toast("Корзина очищена", { type: "success" });
      load();
    });
    load();
  }

  // ============================================================= generate
  async function loadChatGPTStatus(force = false) {
    try {
      S.chatgpt = await api("chatgpt_status", force);
    } catch (error) {
      S.chatgpt = { available: false, signed_in: false, error: error.message };
    }
    return S.chatgpt;
  }

  async function openGenerateModal(key) {
    const lecture = lectureByKey(key);
    if (!lecture) return;
    const entry = openModal(
      `${modalHead("Создать конспект", lecture.title)}
      <div class="modal-body" id="gen-body"><div class="empty" style="padding:40px"><span class="spinner spinner-lg"></span>
        <p class="empty-text" style="margin:12px 0 0">Собираем материалы лекции…</p></div></div>
      <div class="modal-foot" id="gen-foot"><button type="button" class="btn" data-close>Отмена</button></div>`,
      { size: "modal-wide" },
    );
    const modal = entry.modal;
    let options;
    try {
      options = await api("generate_options", key);
    } catch (error) {
      $("#gen-body", modal).innerHTML = `<div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div>`;
      return;
    }
    const chatgpt = S.chatgpt || (await loadChatGPTStatus());
    let selected = "manual";
    const provider = (id) => options.providers.find((item) => item.id === id);
    if (chatgpt.signed_in && !provider("chatgpt").limit_warning) selected = "chatgpt";
    else if (provider("api").configured && !provider("api").limit_warning) selected = "api";
    let handoff = null;

    function providerDescription(item) {
      if (item.id === "chatgpt") {
        if (!chatgpt.available) return "Нужен Codex CLI — см. Настройки → Проверка системы.";
        if (!chatgpt.signed_in) return "Войди в свой аккаунт ChatGPT — подписка Plus/Pro уже включает генерацию.";
        return `Аккаунт ${chatgpt.email || "ChatGPT"} · модель ${item.model}`;
      }
      if (item.id === "api") {
        return item.configured ? `Модель ${item.model}` : "Добавь API-ключ в настройках.";
      }
      return "Скопируешь задание в DeepSeek, ChatGPT или другой чат сам и вставишь ответ сюда.";
    }

    function providerReady(item) {
      if (item.id === "chatgpt") return chatgpt.available && chatgpt.signed_in;
      if (item.id === "api") return item.configured;
      return true;
    }

    function render() {
      const current = provider(selected);
      $("#gen-body", modal).innerHTML = `
        <div class="provider-list">${options.providers
          .map(
            (item) => `<label class="provider ${item.id === selected ? "selected" : ""}">
              <input type="radio" name="gen-provider" value="${item.id}" ${item.id === selected ? "checked" : ""}>
              <span><span class="provider-name">${esc(item.label)}</span>
              <span class="provider-desc" style="display:block">${esc(providerDescription(item))}</span>
              ${item.limit_warning ? `<span class="provider-warn" style="display:block">${esc(item.limit_warning)}</span>` : ""}</span>
              ${
                item.id === "chatgpt" && chatgpt.available && !chatgpt.signed_in
                  ? `<button type="button" class="btn btn-sm" data-login>${icon("user", "icon-sm")}Войти</button>`
                  : item.id === "api" && !item.configured
                    ? `<button type="button" class="btn btn-sm" data-go-settings>Настройки</button>`
                    : ""
              }
            </label>`,
          )
          .join("")}</div>
        ${selected === "manual" ? manualSteps() : transferSummary()}
        ${options.has_lesson ? `<div class="notice" style="margin-top:12px">${icon("info")}<div>У лекции уже есть конспект — новый заменит его.</div></div>` : ""}`;

      const ready = providerReady(current) && !current.limit_warning;
      $("#gen-foot", modal).innerHTML =
        selected === "manual"
          ? `<span class="grow">Ответ чата сохранится как конспект этой лекции.</span>
             <button type="button" class="btn" data-close>Отмена</button>
             <button type="button" class="btn btn-primary" id="gen-save">${icon("check", "icon-sm")}Сохранить конспект</button>`
          : `<span class="grow">Обычно это занимает 1–4 минуты. Можно закрыть окно — работа продолжится.</span>
             <button type="button" class="btn" data-close>Отмена</button>
             <button type="button" class="btn btn-primary" id="gen-start" ${ready ? "" : "disabled"}>${icon("send", "icon-sm")}Отправить и создать</button>`;
      $$("#gen-foot [data-close]", modal).forEach((button) => button.addEventListener("click", entry.close));
      wire();
    }

    function transferSummary() {
      return `<div class="transfer">
        <div class="transfer-box sends"><h4>${icon("send", "icon-sm")}Отправляется (~${options.size_kb} КБ)</h4>
          <ul>${options.sends.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>
        <div class="transfer-box stays"><h4>${icon("shield", "icon-sm")}Остаётся на компьютере</h4>
          <ul>${options.stays.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>
      </div>`;
    }

    function manualSteps() {
      if (!handoff) {
        return `<div class="empty" style="padding:24px"><span class="spinner"></span></div>`;
      }
      const big = handoff.combined.length > 60000;
      return `<div class="handoff-steps">
        <div class="handoff-step"><span class="step-num">1</span><div>
          <div class="step-title">Скопируй задание вместе с материалами лекции</div>
          <div class="btn-row">
            <button type="button" class="btn btn-primary btn-sm" data-copy="combined">${icon("copy", "icon-sm")}Скопировать всё (${Math.round(handoff.combined.length / 1000)} тыс. символов)</button>
            <button type="button" class="btn btn-sm" data-copy="prompt">Только инструкцию</button>
            <button type="button" class="btn btn-ghost btn-sm" data-reveal>${icon("folder", "icon-sm")}Файл с материалами</button>
          </div>
          <div class="field-hint" style="margin-top:6px">${
            big
              ? "Текст большой: если чат его не принимает, скопируй только инструкцию и прикрепи файл lesson-context.md."
              : "Если чат не принимает длинный текст, скопируй инструкцию и прикрепи файл lesson-context.md."
          }</div>
        </div></div>
        <div class="handoff-step"><span class="step-num">2</span><div>
          <div class="step-title">Открой чат, вставь текст и дождись ответа</div>
          <div class="btn-row">${handoff.targets
            .map((target) => `<button type="button" class="btn btn-sm" data-open-url="${esc(target.url)}">${icon("external", "icon-sm")}${esc(target.label)}</button>`)
            .join("")}</div>
        </div></div>
        <div class="handoff-step"><span class="step-num">3</span><div>
          <div class="step-title">Скопируй ответ целиком и вставь сюда</div>
          <textarea class="textarea" id="gen-answer" rows="6" placeholder="# Конспект лекции…"></textarea>
        </div></div>
      </div>`;
    }

    function wire() {
      $$("input[name=gen-provider]", modal).forEach((input) =>
        input.addEventListener("change", async () => {
          selected = input.value;
          render();
          if (selected === "manual" && !handoff) {
            try {
              handoff = await api("web_handoff", key);
            } catch (error) {
              $("#gen-body", modal).insertAdjacentHTML(
                "beforeend",
                `<div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div>`,
              );
              return;
            }
            if (selected === "manual") render();
          }
        }),
      );
      const login = $("[data-login]", modal);
      if (login) {
        login.addEventListener("click", async (event) => {
          event.preventDefault();
          setBusy(login, true, "Ждём вход…");
          try {
            const status = await api("chatgpt_login");
            S.chatgpt = status;
            Object.assign(chatgpt, status);
            if (status.signed_in) selected = "chatgpt";
          } catch (error) {
            toast(error.message, { type: "error" });
          }
          render();
        });
      }
      const settings = $("[data-go-settings]", modal);
      if (settings) {
        settings.addEventListener("click", (event) => {
          event.preventDefault();
          entry.close();
          location.hash = "#/settings";
        });
      }
      $$("[data-copy]", modal).forEach((button) =>
        button.addEventListener("click", async () => {
          const ok = await copyText(button.dataset.copy === "prompt" ? handoff.prompt : handoff.combined);
          toast(ok ? "Скопировано — вставь в чат (Ctrl+V)" : "Не удалось скопировать", { type: ok ? "success" : "error" });
        }),
      );
      const reveal = $("[data-reveal]", modal);
      if (reveal) reveal.addEventListener("click", () => attempt(api("reveal_context_file", key)));
      $$("[data-open-url]", modal).forEach((button) =>
        button.addEventListener("click", () => attempt(api("open_external", button.dataset.openUrl))),
      );
      const start = $("#gen-start", modal);
      if (start) {
        start.addEventListener("click", async () => {
          setBusy(start, true, "Отправляем…");
          try {
            const current = provider(selected);
            await api("generate", key, selected, current.model || "");
            entry.close();
            toast("Создаём конспект — можно заниматься другими делами", { type: "success" });
            reloadLectures();
          } catch (error) {
            setBusy(start, false);
            toast(error.message, { type: "error" });
          }
        });
      }
      const save = $("#gen-save", modal);
      if (save) {
        save.addEventListener("click", async () => {
          const answer = $("#gen-answer", modal);
          if (!answer || !answer.value.trim()) {
            toast("Вставь ответ чата в поле шага 3", { type: "error" });
            return;
          }
          setBusy(save, true, "Сохраняем…");
          try {
            await api("save_lesson", key, answer.value);
            entry.close();
            toast("Конспект сохранён", { type: "success" });
            await reloadLectures();
            location.hash = `#/lecture/${key}`;
            if (currentView && currentView.reload) currentView.reload();
          } catch (error) {
            setBusy(save, false);
            toast(error.message, { type: "error" });
          }
        });
      }
    }

    if (selected === "manual") {
      try {
        handoff = await api("web_handoff", key);
      } catch (error) {
        handoff = null;
      }
    }
    render();
  }

  // ================================================================ player
  const Player = (() => {
    let state = null;
    const SPEEDS = [1, 1.25, 1.5, 1.75, 2];

    function mount(slot, lesson) {
      unmount();
      state = { slot, lesson, audio: null, frameIndex: -1, speed: 1, onTime: null };
    }

    function unmount() {
      if (state && state.audio) {
        state.audio.pause();
        state.audio.removeAttribute("src");
        state.audio.load();
      }
      if (state && state.slot) state.slot.innerHTML = "";
      state = null;
    }

    function frameUrl(seconds) {
      return `${state.lesson.media_base}frame/${Math.max(0, Math.floor(seconds))}`;
    }

    function render() {
      const lesson = state.lesson;
      state.slot.innerHTML = `<div class="player" role="region" aria-label="Запись лекции">
        ${lesson.frame_count ? `<div class="player-thumb" data-p="zoom" title="Кадр с экрана — нажми, чтобы увеличить"><img alt="Кадр лекции"></div>` : "<span></span>"}
        <div class="player-controls">
          <button type="button" class="icon-btn" data-p="back" title="Назад 10 секунд">${icon("back10")}</button>
          <button type="button" class="play-btn" data-p="toggle" aria-label="Воспроизвести">${icon("play")}</button>
          <button type="button" class="icon-btn" data-p="fwd" title="Вперёд 10 секунд">${icon("fwd10")}</button>
        </div>
        <div class="player-track"><span data-p="cur">0:00</span>
          <input type="range" min="0" max="1000" value="0" step="1" aria-label="Позиция" data-p="seek">
          <span data-p="dur">${fmtClock(lesson.duration)}</span></div>
        <button type="button" class="btn btn-ghost btn-sm speed-btn" data-p="speed" title="Скорость">${state.speed}×</button>
        <button type="button" class="icon-btn" data-p="close" title="Закрыть плеер">${icon("x")}</button>
      </div>`;
      const audio = new Audio();
      audio.preload = "auto";
      audio.src = `${lesson.media_base}audio`;
      audio.playbackRate = state.speed;
      state.audio = audio;
      const slot = state.slot;
      const seek = $("[data-p=seek]", slot);
      let dragging = false;
      const duration = () => (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : lesson.duration || 0);
      audio.addEventListener("timeupdate", () => {
        $("[data-p=cur]", slot).textContent = fmtClock(audio.currentTime);
        if (!dragging && duration()) seek.value = String(Math.round((audio.currentTime / duration()) * 1000));
        updateFrame(audio.currentTime);
        if (state.onTime) state.onTime(audio.currentTime);
      });
      audio.addEventListener("loadedmetadata", () => {
        $("[data-p=dur]", slot).textContent = fmtClock(duration());
      });
      const syncButton = () => {
        $("[data-p=toggle]", slot).innerHTML = icon(audio.paused ? "play" : "pause");
        $("[data-p=toggle]", slot).setAttribute("aria-label", audio.paused ? "Воспроизвести" : "Пауза");
      };
      audio.addEventListener("play", syncButton);
      audio.addEventListener("pause", syncButton);
      audio.addEventListener("error", () => toast("Не удалось воспроизвести аудио этой лекции", { type: "error" }));
      seek.addEventListener("input", () => {
        dragging = true;
        $("[data-p=cur]", slot).textContent = fmtClock((Number(seek.value) / 1000) * duration());
      });
      seek.addEventListener("change", () => {
        dragging = false;
        audio.currentTime = (Number(seek.value) / 1000) * duration();
      });
      slot.onclick = (event) => {
        const control = event.target.closest("[data-p]");
        if (!control) return;
        const action = control.dataset.p;
        if (action === "toggle") audio.paused ? audio.play().catch(() => {}) : audio.pause();
        if (action === "back") audio.currentTime = Math.max(0, audio.currentTime - 10);
        if (action === "fwd") audio.currentTime = Math.min(duration() || 1e9, audio.currentTime + 10);
        if (action === "speed") {
          state.speed = SPEEDS[(SPEEDS.indexOf(state.speed) + 1) % SPEEDS.length];
          audio.playbackRate = state.speed;
          control.textContent = `${state.speed}×`;
        }
        if (action === "zoom") showFrame(audio.currentTime);
        if (action === "close") {
          audio.pause();
          slot.innerHTML = "";
          state.audio = null;
        }
      };
    }

    function updateFrame(seconds) {
      if (!state || !state.lesson.frame_count) return;
      const interval = state.lesson.frame_interval || 60;
      const index = Math.round(seconds / interval);
      if (index === state.frameIndex) return;
      state.frameIndex = index;
      const image = $(".player-thumb img", state.slot);
      if (image) image.src = frameUrl(index * interval);
    }

    function playAt(seconds) {
      if (!state) return;
      const lesson = state.lesson;
      if (!lesson.has_audio) {
        if (lesson.frame_count) showFrame(seconds);
        else toast("Записи этой лекции нет на компьютере — подготовь лекцию заново, чтобы слушать с таймкода.");
        return;
      }
      if (!state.audio) render();
      const audio = state.audio;
      const start = () => {
        audio.currentTime = Math.max(0, seconds);
        audio.play().catch(() => {});
      };
      if (audio.readyState >= 1) start();
      else audio.addEventListener("loadedmetadata", start, { once: true });
      updateFrame(seconds);
    }

    function showFrame(seconds) {
      const box = el(`<div class="lightbox" role="dialog" aria-label="Кадр лекции"><img alt="Кадр лекции на ${fmtClock(seconds)}"></div>`);
      $("img", box).src = frameUrl(seconds);
      box.addEventListener("click", () => box.remove());
      document.body.append(box);
    }

    function togglePlay() {
      if (!state || !state.audio) return false;
      state.audio.paused ? state.audio.play().catch(() => {}) : state.audio.pause();
      return true;
    }

    return {
      mount,
      unmount,
      playAt,
      togglePlay,
      onTime(fn) {
        if (state) state.onTime = fn;
      },
      get active() {
        return Boolean(state && state.audio);
      },
    };
  })();

  // ================================================================ lecture
  function LectureView(root, key, initialTab) {
    let lecture = lectureByKey(key);
    if (!lecture) {
      root.innerHTML = `<div class="state-panel"><div class="empty-art">${icon("question")}</div>
        <h2 class="empty-title">Лекция не найдена</h2>
        <p class="empty-text">Возможно, её удалили.</p>
        <button type="button" class="btn" data-go="#/library">${icon("arrowLeft", "icon-sm")}В библиотеку</button></div>`;
      return {};
    }
    let tab = ["lesson", "cards", "transcript"].includes(initialTab) ? initialTab : "lesson";
    let lesson = null;
    let lessonError = "";
    let cardsData = null;
    let transcript = null;
    let editing = false;
    let saveTimer = 0;
    let lastSaved = -1;
    let find = null;
    let session = null;
    let headingOffsets = [];

    root.innerHTML = `<div class="reader">
      <div class="reader-bar">
        <div class="reader-lead">
          <button type="button" class="icon-btn" data-go="#/library" title="Библиотека" aria-label="Назад в библиотеку">${icon("arrowLeft")}</button>
          <div class="reader-title" id="reader-title"></div>
        </div>
        <div class="tabs" role="tablist" id="reader-tabs"></div>
        <div class="reader-tools" id="reader-tools"></div>
        <div class="read-progress"><i id="read-progress"></i></div>
      </div>
      <div class="reader-body" id="reader-body"></div>
      <div id="player-slot"></div>
    </div>`;
    const body = $("#reader-body", root);
    $("#reader-title", root).textContent = lecture.title;
    $("#reader-title", root).title = lecture.title;

    function renderTabs() {
      const cardCount = lecture.cards ? lecture.cards.total : lesson ? lesson.card_count : 0;
      const tabs = [
        { id: "lesson", label: "Конспект", icon: "book" },
        { id: "cards", label: "Карточки", icon: "cards", count: cardCount || "" },
        { id: "transcript", label: "Расшифровка", icon: "lines" },
      ];
      $("#reader-tabs", root).innerHTML = tabs
        .map(
          (item) => `<button type="button" role="tab" class="tab ${tab === item.id ? "active" : ""}" data-tab="${item.id}" aria-selected="${tab === item.id}">
            ${esc(item.label)}${item.count ? `<span class="tab-count">${item.count}</span>` : ""}</button>`,
        )
        .join("");
      const tools = [];
      if (tab === "lesson" && lesson && !editing) {
        tools.push(`<button type="button" class="icon-btn" data-tool="find" title="Найти в тексте (Ctrl+F)">${icon("search")}</button>`);
        tools.push(`<button type="button" class="icon-btn" data-tool="smaller" title="Мельче">${icon("text", "icon-sm")}</button>`);
        tools.push(`<button type="button" class="icon-btn" data-tool="bigger" title="Крупнее">${icon("text")}</button>`);
      }
      tools.push(`<button type="button" class="icon-btn" data-tool="menu" title="Ещё" aria-label="Ещё">${icon("more")}</button>`);
      $("#reader-tools", root).innerHTML = tools.join("");
    }

    $("#reader-tabs", root).addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab]");
      if (!button || button.dataset.tab === tab) return;
      switchTab(button.dataset.tab);
    });

    $("#reader-tools", root).addEventListener("click", (event) => {
      const button = event.target.closest("[data-tool]");
      if (!button) return;
      const tool = button.dataset.tool;
      if (tool === "find") openFind();
      if (tool === "smaller") setPreference("font_scale", Math.round(clamp((S.prefs.font_scale || 1) - 0.05, 0.85, 1.4) * 100) / 100);
      if (tool === "bigger") setPreference("font_scale", Math.round(clamp((S.prefs.font_scale || 1) + 0.05, 0.85, 1.4) * 100) / 100);
      if (tool === "menu") openMenu(button, readerMenu());
    });

    function readerMenu() {
      const items = [];
      if (lesson) {
        items.push({ label: "Редактировать конспект", icon: "edit", run: startEditing });
        items.push({
          label: S.prefs.reading_font === "serif" ? "Шрифт без засечек" : "Шрифт с засечками",
          icon: "text",
          run: () => setPreference("reading_font", S.prefs.reading_font === "serif" ? "sans" : "serif"),
        });
        items.push("-");
      }
      return items.concat(lectureMenuItems(lecture, { inReader: true }));
    }

    function switchTab(next) {
      closeFind();
      if (session) session = null;
      saveReadingPosition();
      tab = next;
      history.replaceState(null, "", `#/lecture/${key}/${tab}`);
      renderTabs();
      renderBody();
    }

    // ---------------------------------------------------------- lesson tab
    async function loadLesson() {
      lesson = null;
      lessonError = "";
      if (lecture.stage !== "lesson") return;
      try {
        lesson = await api("get_lesson", key);
        Player.mount($("#player-slot", root), lesson);
      } catch (error) {
        lessonError = error.message;
      }
    }

    function statePanel() {
      const task = activeTask(key);
      const failed = failedTask(key);
      if (task) {
        const percent = task.status === "running" ? task.percent : 0;
        return `<div class="state-panel">
          <div class="empty-art"><span class="spinner spinner-lg"></span></div>
          <h2 class="empty-title">${task.kind === "generate" ? "Создаём конспект" : "Готовим лекцию"}</h2>
          <div class="progress ${task.status === "running" && percent < 2 ? "indeterminate" : ""}"><div class="progress-bar" style="width:${percent}%"></div></div>
          <p class="state-message">${task.status === "queued" ? "В очереди" : `${percent}% · ${esc(task.cancelling ? "Останавливаем…" : task.message)}`}</p>
          <p class="empty-text">${task.kind === "generate" ? "Обычно это 1–4 минуты." : "Расшифровка идёт на этом компьютере и может занять 10–30 минут. Можно спокойно читать другие лекции."}</p>
          <button type="button" class="btn" data-state="cancel" data-task="${esc(task.id)}">Остановить</button>
        </div>`;
      }
      if (failed) {
        return `<div class="state-panel">
          <div class="empty-art" style="background:var(--danger-soft);color:var(--danger)">${icon("alert")}</div>
          <h2 class="empty-title">Не получилось</h2>
          <p class="empty-text">${esc(failed.error)}</p>
          <button type="button" class="btn btn-primary" data-state="retry">${icon("refresh", "icon-sm")}Попробовать снова</button>
        </div>`;
      }
      if (lecture.stage === "package" || lecture.stage === "prepared") {
        return `<div class="state-panel">
          <div class="empty-art">${icon("sparkles")}</div>
          <h2 class="empty-title">Лекция подготовлена</h2>
          <p class="empty-text">Речь расшифрована, слайды распознаны. Осталось создать конспект — а пока можно просмотреть расшифровку.</p>
          <div class="empty-actions">
            <button type="button" class="btn btn-primary btn-lg" data-state="generate">${icon("sparkles")}Создать конспект</button>
            <button type="button" class="btn btn-lg" data-state="transcript">${icon("lines")}Расшифровка</button>
          </div></div>`;
      }
      return `<div class="state-panel">
        <div class="empty-art">${icon("cpu")}</div>
        <h2 class="empty-title">Сначала подготовим лекцию</h2>
        <p class="empty-text">Расшифруем речь преподавателя и текст со слайдов прямо на этом компьютере — ничего не отправляется. Для полуторачасовой лекции это 10–30 минут.</p>
        <button type="button" class="btn btn-primary btn-lg" data-state="prepare">${icon("cpu")}Подготовить лекцию</button>
      </div>`;
    }

    function renderLesson() {
      if (!lesson) {
        body.innerHTML = lessonError
          ? `<div class="state-panel"><div class="notice notice-danger">${icon("alert")}<div>${esc(lessonError)}</div></div></div>`
          : statePanel();
        $("#read-progress", root).style.width = "0";
        return;
      }
      const tocItems = tocHeadings(lesson.headings);
      const meta = [
        `<span>${icon("clock")}${lesson.reading_minutes} мин чтения</span>`,
        lesson.duration ? `<span>${icon("wave")}Запись ${fmtDuration(lesson.duration)}</span>` : "",
        lesson.card_count ? `<span>${icon("cards")}${countText(lesson.card_count, CARD_FORMS)}</span>` : "",
        lesson.has_audio ? `<span>${icon("play")}Нажимай на таймкоды, чтобы слушать</span>` : "",
      ].join("");
      body.innerHTML = `<div class="reader-scroll" id="reader-scroll">
        <div class="reader-layout ${tocItems.length ? "" : "no-toc"}">
          ${
            tocItems.length
              ? `<nav class="toc" aria-label="Содержание"><div class="toc-title">Содержание</div>${tocItems
                  .map((item) => `<a href="#${esc(item.anchor)}" class="l${item.level}" data-anchor="${esc(item.anchor)}">${esc(item.title)}</a>`)
                  .join("")}</nav>`
              : ""
          }
          <article>
            <header class="article-head"><h1 class="article-title">${esc(lecture.title)}</h1><div class="article-meta">${meta}</div></header>
            <div class="prose" id="prose">${lesson.html}</div>
          </article>
        </div></div>`;
      const prose = $("#prose", body);
      // The lesson usually repeats the lecture title as its first H1.
      const first = prose.firstElementChild;
      if (first && first.tagName === "H1") first.remove();
      const scroller = $("#reader-scroll", body);
      measureHeadings();
      scroller.addEventListener("scroll", onScroll, { passive: true });
      $$(".toc a", body).forEach((link) =>
        link.addEventListener("click", (event) => {
          event.preventDefault();
          const target = document.getElementById(link.dataset.anchor);
          if (target) scroller.scrollTo({ top: target.offsetTop - 16, behavior: "smooth" });
        }),
      );
      const position = lesson.reading_position || 0;
      if (position > 0.02 && position < 0.98) {
        requestAnimationFrame(() => {
          scroller.style.scrollBehavior = "auto";
          scroller.scrollTop = position * (scroller.scrollHeight - scroller.clientHeight);
          scroller.style.scrollBehavior = "";
          onScroll();
        });
      } else {
        onScroll();
      }
      if (pendingFind) {
        const query = pendingFind;
        pendingFind = "";
        setTimeout(() => openFind(query), 60);
      }
    }

    function tocHeadings(headings) {
      const levels = headings.map((item) => item.level).filter((level) => level > 1);
      if (!levels.length) return [];
      const top = Math.min(...levels);
      const list = headings.filter((item) => item.level >= top && item.level <= top + 1);
      return list.length >= 2 ? list : [];
    }

    function measureHeadings() {
      const scroller = $("#reader-scroll", body);
      if (!scroller) return;
      headingOffsets = $$(".toc a", body)
        .map((link) => {
          const target = document.getElementById(link.dataset.anchor);
          return target ? { link, top: target.offsetTop } : null;
        })
        .filter(Boolean);
    }

    let scrollFrame = 0;
    function onScroll() {
      if (scrollFrame) return;
      scrollFrame = requestAnimationFrame(() => {
        scrollFrame = 0;
        const scroller = $("#reader-scroll", body);
        if (!scroller) return;
        const max = scroller.scrollHeight - scroller.clientHeight;
        const ratio = max > 0 ? scroller.scrollTop / max : 1;
        $("#read-progress", root).style.width = `${Math.round(ratio * 1000) / 10}%`;
        let current = null;
        for (const item of headingOffsets) {
          if (item.top - 90 <= scroller.scrollTop) current = item;
        }
        headingOffsets.forEach((item) => item.link.classList.toggle("active", item === current));
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveReadingPosition, 1200);
      });
    }

    function saveReadingPosition() {
      clearTimeout(saveTimer);
      const scroller = $("#reader-scroll", body);
      if (!scroller || !lesson) return;
      const max = scroller.scrollHeight - scroller.clientHeight;
      const ratio = max > 0 ? scroller.scrollTop / max : 1;
      if (Math.abs(ratio - lastSaved) < 0.005) return;
      lastSaved = ratio;
      lesson.reading_position = ratio;
      api("set_reading_position", key, ratio).catch(() => {});
    }

    body.addEventListener("click", (event) => {
      // A study session handles its own clicks.
      if (event.target.closest(".study")) return;
      const timestamp = event.target.closest("button.ts");
      if (timestamp) {
        Player.playAt(Number(timestamp.dataset.t) || 0);
        return;
      }
      const action = event.target.closest("[data-state]");
      if (!action) return;
      const name = action.dataset.state;
      if (name === "cancel") attempt(api("cancel_task", action.dataset.task));
      if (name === "retry") retryFailed(lecture);
      if (name === "generate") openGenerateModal(key);
      if (name === "prepare") startPrepare(key);
      if (name === "transcript") switchTab("transcript");
    });

    // ------------------------------------------------------------- editing
    function startEditing() {
      if (!lesson) return;
      closeFind();
      editing = true;
      tab = "lesson";
      renderTabs();
      body.innerHTML = `<div class="editor">
        <div class="editor-head"><span class="grow">Редактирование в формате Markdown: <code>## Заголовок</code>, <code>**жирный**</code>, <code>- список</code>.</span>
          <button type="button" class="btn" data-edit="cancel">Отмена</button>
          <button type="button" class="btn btn-primary" data-edit="save">${icon("check", "icon-sm")}Сохранить</button></div>
        <textarea id="lesson-editor" spellcheck="true"></textarea></div>`;
      const area = $("#lesson-editor", body);
      area.value = lesson.markdown;
      area.focus();
      $("[data-edit=cancel]", body).addEventListener("click", async () => {
        if (area.value !== lesson.markdown) {
          const ok = await confirmDialog({ title: "Отменить изменения?", text: "Правки не сохранятся.", confirm: "Не сохранять", danger: true });
          if (!ok) return;
        }
        editing = false;
        renderTabs();
        renderLesson();
      });
      $("[data-edit=save]", body).addEventListener("click", async (event) => {
        setBusy(event.currentTarget, true, "Сохраняем…");
        try {
          await api("save_lesson", key, area.value);
          editing = false;
          toast("Конспект сохранён", { type: "success" });
          await reload();
        } catch (error) {
          setBusy(event.currentTarget, false);
          toast(error.message, { type: "error" });
        }
      });
    }

    // ---------------------------------------------------------------- find
    function openFind(initial = "") {
      if (tab !== "lesson" || !lesson || editing) return;
      if (!find) {
        const bar = el(`<div class="find-bar" role="search">
          ${icon("search", "icon-sm")}
          <input class="input" type="search" placeholder="Найти в конспекте" aria-label="Найти в конспекте">
          <span class="find-count"></span>
          <button type="button" class="icon-btn" data-find="prev" title="Предыдущее (Shift+Enter)">${icon("chevronUp")}</button>
          <button type="button" class="icon-btn" data-find="next" title="Следующее (Enter)">${icon("chevronDown")}</button>
          <button type="button" class="icon-btn" data-find="close" title="Закрыть (Esc)">${icon("x")}</button>
        </div>`);
        body.append(bar);
        find = { bar, hits: [], index: -1 };
        const input = $("input", bar);
        const run = debounce(() => highlightFind(input.value), 120);
        input.addEventListener("input", run);
        input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            stepFind(event.shiftKey ? -1 : 1);
          }
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            closeFind();
          }
        });
        bar.addEventListener("click", (event) => {
          const button = event.target.closest("[data-find]");
          if (!button) return;
          if (button.dataset.find === "close") closeFind();
          else stepFind(button.dataset.find === "prev" ? -1 : 1);
        });
      }
      const input = $("input", find.bar);
      if (initial) {
        input.value = initial;
        highlightFind(initial);
      }
      input.focus();
      input.select();
    }

    function clearHighlights() {
      const prose = $("#prose", body);
      if (!prose) return;
      $$("mark.find-hit", prose).forEach((mark) => {
        const parent = mark.parentNode;
        parent.replaceChild(document.createTextNode(mark.textContent), mark);
        parent.normalize();
      });
    }

    function highlightFind(query) {
      clearHighlights();
      find.hits = [];
      find.index = -1;
      const prose = $("#prose", body);
      const needle = query.trim().toLocaleLowerCase("ru");
      if (!prose || needle.length < 1) {
        $(".find-count", find.bar).textContent = "";
        return;
      }
      const walker = document.createTreeWalker(prose, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const node of nodes) {
        const text = node.nodeValue;
        const lower = text.toLocaleLowerCase("ru");
        let index = lower.indexOf(needle);
        if (index < 0) continue;
        const fragment = document.createDocumentFragment();
        let last = 0;
        while (index >= 0) {
          fragment.append(text.slice(last, index));
          const mark = document.createElement("mark");
          mark.className = "find-hit";
          mark.textContent = text.slice(index, index + needle.length);
          fragment.append(mark);
          find.hits.push(mark);
          last = index + needle.length;
          index = lower.indexOf(needle, last);
        }
        fragment.append(text.slice(last));
        node.parentNode.replaceChild(fragment, node);
        if (find.hits.length > 2000) break;
      }
      stepFind(1);
    }

    function stepFind(direction) {
      if (!find) return;
      const count = find.hits.length;
      const counter = $(".find-count", find.bar);
      if (!count) {
        counter.textContent = $("input", find.bar).value.trim() ? "0 из 0" : "";
        return;
      }
      if (find.index >= 0) find.hits[find.index].classList.remove("current");
      find.index = (find.index + direction + count) % count;
      const mark = find.hits[find.index];
      mark.classList.add("current");
      counter.textContent = `${find.index + 1} из ${count}`;
      const scroller = $("#reader-scroll", body);
      const top = mark.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      scroller.scrollTo({ top: top - scroller.clientHeight / 3, behavior: "smooth" });
    }

    function closeFind() {
      if (!find) return;
      clearHighlights();
      find.bar.remove();
      find = null;
    }

    // --------------------------------------------------------------- cards
    async function renderCards() {
      if (lecture.stage !== "lesson") {
        body.innerHTML = `<div class="state-panel"><div class="empty-art">${icon("cards")}</div>
          <h2 class="empty-title">Карточки появятся вместе с конспектом</h2>
          <p class="empty-text">Из вопросов для самопроверки и словаря терминов получатся карточки для повторения.</p></div>`;
        return;
      }
      if (!cardsData) {
        body.innerHTML = `<div class="state-panel"><span class="spinner spinner-lg"></span></div>`;
        try {
          cardsData = await api("get_cards", key);
        } catch (error) {
          body.innerHTML = `<div class="state-panel"><div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div></div>`;
          return;
        }
        if (tab !== "cards") return;
      }
      if (session) return;
      const { cards, stats } = cardsData;
      if (!cards.length) {
        body.innerHTML = `<div class="state-panel"><div class="empty-art">${icon("cards")}</div>
          <h2 class="empty-title">В этом конспекте нет карточек</h2>
          <p class="empty-text">Карточки строятся из разделов «Вопросы для самопроверки», «Ключевые понятия» и «Словарь». Создай конспект заново — новые конспекты содержат ответы на вопросы.</p></div>`;
        return;
      }
      const due = cards.filter((card) => card.due);
      body.innerHTML = `<div class="reader-scroll"><div class="deck">
        <div class="deck-stats">
          <div class="stat accent"><div class="stat-value">${stats.due}</div><div class="stat-label">к повторению сегодня</div></div>
          <div class="stat"><div class="stat-value">${stats.new}</div><div class="stat-label">новых</div></div>
          <div class="stat"><div class="stat-value">${stats.learning}</div><div class="stat-label">в процессе</div></div>
          <div class="stat success"><div class="stat-value">${stats.known}</div><div class="stat-label">выучено</div></div>
        </div>
        <div class="deck-actions">
          ${
            due.length
              ? `<button type="button" class="btn btn-primary btn-lg" data-deck="due">${icon("play")}Повторить ${countText(due.length, CARD_FORMS)}</button>`
              : `<div class="notice notice-info" style="flex:1">${icon("check")}<div>На сегодня всё повторено. Новые повторения появятся завтра.</div></div>`
          }
          <button type="button" class="btn btn-lg" data-deck="all">${icon("flip")}Пройти все</button>
          <span class="grow"></span>
          <button type="button" class="btn btn-ghost btn-sm" data-deck="reset">Сбросить прогресс</button>
        </div>
        <div class="section-title">Все карточки</div>
        <div class="card-list">${cards
          .map(
            (card) => `<details class="card-item">
              <summary>${icon("chevronRight", "icon-sm chev")}<div class="card-q">${card.front_html}
                <div class="box-dots" title="Уровень запоминания">${[1, 2, 3, 4, 5].map((n) => `<i class="${card.box >= n ? "on" : ""}"></i>`).join("")}</div></div>
                <span class="pill">${card.kind === "term" ? "Термин" : "Вопрос"}</span></summary>
              <div class="card-a prose">${card.back_html || '<p class="faint">Ответа в конспекте нет — проверь себя по тексту раздела.</p>'}</div>
            </details>`,
          )
          .join("")}</div>
      </div></div>`;
      body.onclick = null;
      $$("[data-deck]", body).forEach((button) =>
        button.addEventListener("click", async () => {
          const action = button.dataset.deck;
          if (action === "reset") {
            const ok = await confirmDialog({
              title: "Сбросить прогресс?",
              text: "Все карточки этой лекции снова станут новыми.",
              confirm: "Сбросить",
              danger: true,
            });
            if (!ok) return;
            await attempt(api("reset_cards", key));
            cardsData = null;
            renderCards();
            return;
          }
          const queue = action === "due" ? due : shuffle(cards.slice());
          session = StudySession(body, queue.map((card) => ({ ...card, lecture_key: key })), {
            onExit: () => {
              session = null;
              cardsData = null;
              renderCards();
              reloadLectures();
            },
          });
        }),
      );
    }

    // ---------------------------------------------------------- transcript
    async function renderTranscript() {
      if (!transcript) {
        body.innerHTML = `<div class="state-panel"><span class="spinner spinner-lg"></span></div>`;
        try {
          transcript = await api("get_transcript", key);
        } catch (error) {
          body.innerHTML = `<div class="state-panel"><div class="empty-art">${icon("lines")}</div>
            <h2 class="empty-title">Расшифровки пока нет</h2><p class="empty-text">${esc(error.message)}</p></div>`;
          return;
        }
        if (tab !== "transcript") return;
      }
      if (!lesson && lecture.stage !== "lesson") {
        // Without a lesson the player still needs media info.
        Player.mount($("#player-slot", root), {
          has_audio: true,
          frame_count: 0,
          frame_interval: 60,
          duration: transcript.segments.length ? transcript.segments[transcript.segments.length - 1].end : 0,
          media_base: `media/${key}/`,
        });
      }
      body.innerHTML = `<div class="reader-scroll" id="transcript-scroll"><div class="transcript">
        <div class="transcript-tools"><div class="search-box">${icon("search")}
          <input class="input" type="search" id="transcript-filter" placeholder="Поиск по расшифровке"></div></div>
        <div id="segments"></div></div></div>`;
      const box = $("#segments", body);
      const filter = $("#transcript-filter", body);
      const draw = () => {
        const query = filter.value.trim();
        const lower = query.toLocaleLowerCase("ru");
        const items = transcript.segments.filter((segment) => !lower || segment.text.toLocaleLowerCase("ru").includes(lower));
        box.innerHTML = items.length
          ? items
              .map(
                (segment) => `<div class="seg" data-start="${segment.start}">
                  <button type="button" class="ts" data-t="${Math.floor(segment.start)}">${fmtClock(segment.start)}</button>
                  <div>${highlight(segment.text, query)}</div></div>`,
              )
              .join("")
          : `<p class="faint" style="padding:12px">Ничего не найдено.</p>`;
      };
      filter.addEventListener("input", debounce(draw, 120));
      draw();
      let playing = null;
      Player.onTime((time) => {
        if (tab !== "transcript") return;
        const rows = $$(".seg", box);
        let current = null;
        for (const row of rows) {
          if (Number(row.dataset.start) <= time) current = row;
          else break;
        }
        if (current !== playing) {
          if (playing) playing.classList.remove("playing");
          if (current) current.classList.add("playing");
          playing = current;
        }
      });
    }

    function renderBody() {
      body.onclick = null;
      if (tab === "cards") renderCards();
      else if (tab === "transcript") renderTranscript();
      else renderLesson();
    }

    async function reload() {
      lecture = lectureByKey(key) || lecture;
      cardsData = null;
      await loadLesson();
      renderTabs();
      renderBody();
    }

    let lastStage = lecture.stage;
    let lastTaskSignature = "";
    let wasGenerating = false;
    renderTabs();
    body.innerHTML = `<div class="state-panel"><span class="spinner spinner-lg"></span></div>`;
    loadLesson().then(() => {
      renderTabs();
      renderBody();
    });

    return {
      refresh() {
        const fresh = lectureByKey(key);
        if (!fresh) return;
        lecture = fresh;
        if (fresh.stage !== lastStage) {
          lastStage = fresh.stage;
          if (!editing) reload();
          return;
        }
        const running = activeTask(key);
        const generating = Boolean(running && running.kind === "generate");
        if (wasGenerating && !generating && lesson && !editing && tab === "lesson") {
          // A regenerated lesson replaced the one on screen.
          wasGenerating = false;
          reload();
          return;
        }
        wasGenerating = generating;
        const task = running || failedTask(key);
        const signature = task ? `${task.id}:${task.status}:${task.percent}:${task.message}` : "";
        if (signature !== lastTaskSignature) {
          lastTaskSignature = signature;
          if (!lesson && tab === "lesson" && !editing) renderLesson();
        }
      },
      reload,
      renamed(title) {
        lecture = { ...lecture, title };
        $("#reader-title", root).textContent = title;
        const heading = $(".article-title", body);
        if (heading) heading.textContent = title;
      },
      openFind,
      closeFind() {
        if (find) {
          closeFind();
          return true;
        }
        return false;
      },
      session: () => session,
      leave() {
        saveReadingPosition();
        Player.unmount();
      },
    };
  }

  function shuffle(items) {
    for (let i = items.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [items[i], items[j]] = [items[j], items[i]];
    }
    return items;
  }

  // ========================================================= study session
  function StudySession(container, cards, { onExit, showLecture = false }) {
    const queue = cards.slice();
    const total = queue.length;
    let done = 0;
    let remembered = 0;
    let forgotten = 0;
    let revealed = false;
    const retried = new Set();
    const forgottenCards = [];

    function current() {
      return queue[0];
    }

    function render() {
      const card = current();
      if (!card) {
        renderDone();
        return;
      }
      const percent = Math.round((done / Math.max(1, total)) * 100);
      const back = card.back_html
        ? `${card.hint ? `<div class="flash-hint-label">${icon("info", "icon-sm")}Подсказка из конспекта — точного ответа в нём нет</div>` : ""}<div class="prose">${card.back_html}</div>`
        : `<p class="muted" style="margin:0">Ответа в конспекте нет. Найди его в разделе «${esc(card.section || "конспект")}» и оцени себя честно.</p>`;
      container.innerHTML = `<div class="reader-scroll"><div class="study">
        <div class="study-top">
          <button type="button" class="btn btn-ghost btn-sm" data-s="exit">${icon("arrowLeft", "icon-sm")}Закончить</button>
          <div class="progress"><div class="progress-bar" style="width:${percent}%"></div></div>
          <span>${Math.min(done + 1, total)} / ${total}</span>
        </div>
        <div class="flashcard">
          ${card.section ? `<div class="flash-section">${esc(card.section)}</div>` : ""}
          <div class="flash-front">${card.front_html}</div>
          <div class="flash-spacer"></div>
          ${revealed ? `<div class="flash-back">${back}</div>` : ""}
        </div>
        <div class="flash-actions">${
          revealed
            ? `<button type="button" class="btn btn-lg btn-forgot" data-s="forgot">${icon("x")}Не помню <kbd>1</kbd></button>
               <button type="button" class="btn btn-lg btn-remember" data-s="remember">${icon("check")}Помню <kbd>2</kbd></button>`
            : `<button type="button" class="btn btn-lg btn-primary" data-s="reveal">Показать ответ <kbd>Пробел</kbd></button>`
        }</div>
        ${
          showLecture
            ? `<div class="flash-lecture">Лекция: <button type="button" data-s="lecture">${esc(card.lecture_title || "")}</button></div>`
            : ""
        }
        ${card.timestamps && card.timestamps.length && !showLecture ? `<div class="flash-lecture">Есть в записи: ${card.timestamps.slice(0, 3).map((t) => `<button type="button" class="ts" data-t="${Math.floor(t)}">${fmtClock(t)}</button>`).join(" ")}</div>` : ""}
      </div></div>`;
      // Keys are handled globally; a focused button would also react to Space.
      if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    }

    function renderDone() {
      const retry = forgottenCards.length
        ? `<button type="button" class="btn btn-lg" data-s="again">${icon("refresh")}Повторить забытые (${forgottenCards.length})</button>`
        : "";
      container.innerHTML = `<div class="reader-scroll"><div class="study"><div class="study-done">
        <div class="empty-art" style="margin:0 auto 18px;background:var(--success-soft);color:var(--success)">${icon("check")}</div>
        <h2 class="empty-title">Готово!</h2>
        <p class="empty-text" style="margin-left:auto;margin-right:auto">Пройдено ${countText(total, CARD_FORMS)}. Помню с первого раза: ${remembered}, пришлось повторить: ${forgotten}.
        Следующее повторение запланировано автоматически.</p>
        <div class="empty-actions" style="justify-content:center">${retry}
          <button type="button" class="btn btn-primary btn-lg" data-s="exit">Готово</button></div>
      </div></div></div>`;
    }

    function grade(ok) {
      const card = queue.shift();
      api("review_card", card.lecture_key, card.id, ok).catch((error) => toast(error.message, { type: "error" }));
      if (ok) {
        if (!retried.has(card.id)) remembered += 1;
        done += 1;
      } else {
        if (!retried.has(card.id)) {
          forgotten += 1;
          forgottenCards.push(card);
        }
        retried.add(card.id);
        // Show a forgotten card again a few cards later in the same session.
        queue.splice(Math.min(queue.length, 3), 0, card);
      }
      revealed = false;
      render();
    }

    container.onclick = (event) => {
      const timestamp = event.target.closest("button.ts");
      if (timestamp) {
        Player.playAt(Number(timestamp.dataset.t) || 0);
        return;
      }
      const control = event.target.closest("[data-s]");
      if (!control) return;
      const action = control.dataset.s;
      if (action === "reveal") {
        revealed = true;
        render();
      }
      if (action === "forgot") grade(false);
      if (action === "remember") grade(true);
      if (action === "exit") onExit();
      if (action === "lecture") location.hash = `#/lecture/${current().lecture_key}`;
      if (action === "again") {
        container.onclick = null;
        activeSession = StudySession(container, forgottenCards.slice(), { onExit, showLecture });
      }
    };

    const api_ = {
      key(event) {
        if (!current()) {
          if (event.key === "Enter" || event.key === "Escape") {
            onExit();
            return true;
          }
          return false;
        }
        if (!revealed && (event.key === " " || event.key === "Enter")) {
          revealed = true;
          render();
          return true;
        }
        if (revealed && (event.key === "1" || event.key === "ArrowLeft")) {
          grade(false);
          return true;
        }
        if (revealed && (event.key === "2" || event.key === "ArrowRight")) {
          grade(true);
          return true;
        }
        return false;
      },
    };
    activeSession = api_;
    render();
    return api_;
  }

  let activeSession = null;

  // ================================================================ review
  function ReviewView(root) {
    root.innerHTML = `<div class="page" id="review-page"><div class="page-inner" id="review-inner">
      <div class="state-panel"><span class="spinner spinner-lg"></span></div></div></div>`;
    const inner = $("#review-inner", root);
    let data = null;

    async function load() {
      try {
        data = await api("review_queue", true, 80);
      } catch (error) {
        inner.innerHTML = `<div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div>`;
        return;
      }
      render();
    }

    function render() {
      activeSession = null;
      const lectures = data.lectures;
      const stats = lectures.reduce(
        (sum, item) => {
          for (const name of ["total", "new", "learning", "known", "due"]) sum[name] += item.stats[name];
          return sum;
        },
        { total: 0, new: 0, learning: 0, known: 0, due: 0 },
      );
      if (!lectures.length) {
        inner.innerHTML = `<div class="page-head"><div><h1 class="page-title">Повторение</h1></div></div>
          <div class="empty"><div class="empty-art">${icon("cards")}</div>
          <h2 class="empty-title">Карточек пока нет</h2>
          <p class="empty-text">Когда появятся конспекты, вопросы для самопроверки и термины из них превратятся в карточки. Повторяй их здесь — интервалы подбираются автоматически.</p>
          <button type="button" class="btn btn-primary" data-go="#/library">В библиотеку</button></div>`;
        return;
      }
      inner.innerHTML = `<div class="page-head"><div><h1 class="page-title">Повторение</h1>
          <p class="page-sub">Карточки из всех конспектов. Что помнишь — вернётся через несколько дней, что забыл — повторим скоро.</p></div></div>
        <div class="deck-stats">
          <div class="stat accent"><div class="stat-value">${data.total_due}</div><div class="stat-label">к повторению сегодня</div></div>
          <div class="stat"><div class="stat-value">${stats.new}</div><div class="stat-label">новых</div></div>
          <div class="stat"><div class="stat-value">${stats.learning}</div><div class="stat-label">в процессе</div></div>
          <div class="stat success"><div class="stat-value">${stats.known}</div><div class="stat-label">выучено</div></div>
        </div>
        <div class="deck-actions">${
          data.cards.length
            ? `<button type="button" class="btn btn-primary btn-lg" data-review="start">${icon("play")}Начать: ${countText(data.cards.length, CARD_FORMS)}</button>
               <span class="faint small">${data.total_due > data.cards.length ? `Всего к повторению ${data.total_due} — берём по ${data.cards.length} за раз.` : ""}</span>`
            : `<div class="notice notice-info" style="flex:1">${icon("check")}<div>На сегодня всё. Возвращайся завтра — карточки появятся по расписанию.</div></div>`
        }</div>
        <div class="section-title">По лекциям</div>
        <div class="lecture-list">${lectures
          .map((item) => {
            const share = item.stats.total ? Math.round((item.stats.known / item.stats.total) * 100) : 0;
            return `<div class="lecture-row clickable" data-review-lecture="${esc(item.key)}">
              <div class="lecture-icon ${item.stats.due ? "next" : "ready"}">${icon("cards", "icon-lg")}</div>
              <div class="lecture-main"><div class="lecture-title">${esc(item.title)}</div>
                <div class="lecture-meta"><span>${countText(item.stats.total, CARD_FORMS)}</span><span>выучено ${share}%</span>
                <span class="mini-progress" style="width:90px"><i style="width:${share}%"></i></span></div></div>
              <div class="lecture-actions">${item.stats.due ? `<span class="pill pill-accent">${item.stats.due} сегодня</span>` : `<span class="pill pill-success">${icon("check", "icon-sm")}повторено</span>`}</div>
            </div>`;
          })
          .join("")}</div>`;
    }

    $("#review-page", root).addEventListener("click", (event) => {
      const start = event.target.closest("[data-review=start]");
      if (start) {
        const page = $("#review-page", root);
        page.innerHTML = "";
        StudySession(page, data.cards, {
          showLecture: true,
          onExit: () => {
            page.innerHTML = `<div class="page-inner" id="review-inner"></div>`;
            reloadLectures();
            route();
          },
        });
        return;
      }
      const lecture = event.target.closest("[data-review-lecture]");
      if (lecture) location.hash = `#/lecture/${lecture.dataset.reviewLecture}/cards`;
    });

    load();
    return {
      leave() {
        activeSession = null;
      },
    };
  }

  // =================================================================== chat
  const CHAT_SUGGESTIONS = [
    "Объясни простыми словами, как работает градиентный спуск",
    "Составь план подготовки к экзамену на неделю",
    "Придумай 5 вопросов для самопроверки по теме",
    "Проверь моё решение задачи и найди ошибки",
  ];

  function chatDate(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const now = new Date();
    const day = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
    const days = Math.round((day(now) - day(date)) / 86400000);
    if (days <= 0) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (days === 1) return "вчера";
    const short = `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;
    return date.getFullYear() === now.getFullYear() ? short : `${short}.${date.getFullYear()}`;
  }

  function ChatView(root, initialId) {
    root.innerHTML = `<div class="chat-layout">
      <aside class="chat-side" aria-label="Чаты">
        <div class="chat-side-head">
          <span class="chat-side-title">Чаты</span>
          <button type="button" class="btn btn-sm" id="chat-new">${icon("plus", "icon-sm")}Новый чат</button>
        </div>
        <div class="chat-list" id="chat-list"></div>
      </aside>
      <section class="chat-main">
        <header class="chat-head" id="chat-head"></header>
        <div class="chat-scroll" id="chat-scroll"><div class="chat-thread" id="chat-thread"></div></div>
        <div class="chat-composer-wrap">
          <form class="chat-composer" id="chat-form">
            <textarea id="chat-input" rows="1" placeholder="Напиши сообщение…" aria-label="Сообщение"></textarea>
            <button type="submit" class="chat-send" id="chat-send" aria-label="Отправить" title="Отправить (Enter)">${icon("send")}</button>
          </form>
          <div class="chat-note" id="chat-note"></div>
        </div>
      </section>
    </div>`;
    const list = $("#chat-list", root);
    const head = $("#chat-head", root);
    const scroller = $("#chat-scroll", root);
    const thread = $("#chat-thread", root);
    const form = $("#chat-form", root);
    const input = $("#chat-input", root);
    const sendButton = $("#chat-send", root);
    const note = $("#chat-note", root);

    let meta = S.chatMeta;
    let chatId = initialId || null;
    let chat = null;
    let running = false;
    let error = "";
    let alive = true;
    let pollToken = 0;
    let loadToken = 0;

    const configured = () => Boolean(meta ? meta.api_configured : S.settings && S.settings.api_configured);
    const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
    const scrollToBottom = () => {
      scroller.scrollTop = scroller.scrollHeight;
    };

    async function loadMeta() {
      try {
        meta = await api("chat_list");
      } catch (err) {
        if (alive) toast(err.message, { type: "error" });
        return;
      }
      if (!alive) return;
      S.chatMeta = meta;
      renderList();
      renderHead();
      renderNote();
      updateComposer();
      if (!chatId) renderThread();
    }

    function renderList() {
      const chats = (meta && meta.chats) || [];
      if (!chats.length) {
        list.innerHTML = `<div class="chat-list-empty">Здесь появятся твои разговоры.</div>`;
        return;
      }
      list.innerHTML = chats
        .map(
          (item) => `<button type="button" class="chat-item ${item.id === chatId ? "active" : ""}" data-chat="${esc(item.id)}" title="${esc(item.title)}">
            <span class="chat-item-title">${esc(item.title)}</span>
            <span class="chat-item-date">${esc(chatDate(item.updated_at))}</span></button>`,
        )
        .join("");
    }

    function renderHead() {
      const title = chat ? chat.title : "Новый чат";
      const pill =
        meta && meta.api_configured
          ? `<span class="pill" title="Провайдер и модель из настроек">${esc(meta.provider_label)} · ${esc(meta.model)}</span>`
          : "";
      head.innerHTML = `<div class="chat-head-main"><h1 class="chat-title">${esc(title)}</h1>${pill}</div>
        ${chat ? `<button type="button" class="icon-btn" id="chat-menu" aria-label="Действия с чатом">${icon("more")}</button>` : ""}`;
    }

    function renderNote() {
      note.textContent =
        configured() && meta
          ? `Сообщения отправляются в ${meta.provider_label} по API-ключу. Ответы модели могут содержать ошибки.`
          : "";
    }

    function updateComposer() {
      const ready = configured();
      input.disabled = !ready;
      input.placeholder = ready ? "Напиши сообщение…" : "Добавь API-ключ в настройках, чтобы начать";
      sendButton.classList.toggle("stop", running);
      sendButton.innerHTML = icon(running ? "stop" : "send");
      sendButton.setAttribute("aria-label", running ? "Остановить ответ" : "Отправить");
      sendButton.title = running ? "Остановить ответ" : "Отправить (Enter)";
      sendButton.disabled = running ? false : !ready || !input.value.trim();
    }

    function autosize() {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
    }

    function welcomeHtml() {
      const ready = configured();
      return `<div class="chat-welcome">
        <div class="empty-art">${icon("chat")}</div>
        <h2 class="empty-title">Чем помочь?</h2>
        <p class="empty-text">Спроси что угодно: объясни тему, разбери задачу или составь план повторения.</p>
        ${
          ready
            ? `<div class="chat-suggestions">${CHAT_SUGGESTIONS.map(
                (text) => `<button type="button" class="chat-suggestion" data-suggest="${esc(text)}">${esc(text)}</button>`,
              ).join("")}</div>`
            : `<div class="notice notice-info chat-setup">${icon("key")}<div>Чат работает через API-ключ OpenAI, DeepSeek или Claude. Добавь ключ и модель в настройках.</div>
                <button type="button" class="btn btn-sm btn-primary" data-go="#/settings">Открыть настройки</button></div>`
        }
      </div>`;
    }

    function messageHtml(message, isLast) {
      const copy = `<button type="button" class="icon-btn" data-copy="${esc(message.id)}" aria-label="Копировать" title="Копировать">${icon("copy", "icon-sm")}</button>`;
      if (message.role === "user") {
        return `<div class="msg msg-user"><div class="msg-bubble">${esc(message.content)}</div>
          <div class="msg-actions">${copy}</div></div>`;
      }
      const retry =
        isLast && !running
          ? `<button type="button" class="icon-btn" data-chat-retry aria-label="Ответить заново" title="Ответить заново">${icon("refresh", "icon-sm")}</button>`
          : "";
      return `<div class="msg msg-assistant ${isLast ? "last" : ""}">
        <div class="msg-body prose">${message.html}</div>
        ${message.stopped ? `<div class="msg-flag">${icon("stop", "icon-sm")}Ответ остановлен</div>` : ""}
        <div class="msg-actions">${copy}${retry}${message.model ? `<span class="msg-model">${esc(message.model)}</span>` : ""}</div></div>`;
    }

    const streamHtml = () => `<div class="msg msg-assistant streaming" id="chat-stream">
      <div class="msg-body prose"></div><div class="msg-typing"><span class="typing"><i></i><i></i><i></i></span></div></div>`;

    function renderThread() {
      if (!chat) {
        thread.innerHTML = welcomeHtml();
        return;
      }
      const messages = chat.messages;
      let html = messages.map((message, index) => messageHtml(message, index === messages.length - 1)).join("");
      const last = messages[messages.length - 1];
      if (running) html += streamHtml();
      else if (error) {
        html += `<div class="notice notice-danger chat-error">${icon("alert")}<div class="chat-error-text">${esc(error)}</div>
          <button type="button" class="btn btn-sm" data-chat-retry>${icon("refresh", "icon-sm")}Повторить</button></div>`;
      } else if (last && last.role === "user") {
        html += `<div class="chat-missing"><span class="faint small">Ответа на это сообщение нет.</span>
          <button type="button" class="btn btn-sm btn-ghost" data-chat-retry>${icon("refresh", "icon-sm")}Получить ответ</button></div>`;
      }
      thread.innerHTML = html;
    }

    function applyChat(data) {
      chat = data;
      chatId = data.id;
      running = Boolean(data.running);
      error = data.error || "";
      renderHead();
      renderThread();
      renderList();
      updateComposer();
      scrollToBottom();
      if (running) follow(data.id);
    }

    async function openChat(id) {
      pollToken += 1;
      const token = ++loadToken;
      chatId = id || null;
      chat = null;
      running = false;
      error = "";
      renderList();
      renderHead();
      updateComposer();
      if (!chatId) {
        renderThread();
        input.focus();
        return;
      }
      thread.innerHTML = `<div class="state-panel"><span class="spinner spinner-lg"></span></div>`;
      try {
        const data = await api("chat_get", chatId);
        if (!alive || token !== loadToken) return;
        applyChat(data);
        input.focus();
      } catch (err) {
        if (!alive || token !== loadToken) return;
        thread.innerHTML = `<div class="notice notice-danger">${icon("alert")}<div>${esc(err.message)}</div></div>`;
      }
    }

    async function follow(id) {
      const token = ++pollToken;
      let seen = 0;
      while (alive && token === pollToken) {
        let result;
        try {
          result = await api("chat_poll", id, seen);
        } catch (err) {
          if (!alive || token !== pollToken) return;
          running = false;
          error = err.message;
          renderThread();
          updateComposer();
          return;
        }
        if (!alive || token !== pollToken) return;
        if (result.running) {
          seen = result.length;
          const body = $("#chat-stream .msg-body", thread);
          if (body) {
            const stick = nearBottom();
            body.innerHTML = result.html;
            if (stick) scrollToBottom();
          }
          await sleep(60);
          continue;
        }
        const stick = nearBottom();
        chat = result.chat;
        running = false;
        error = result.error || "";
        renderThread();
        updateComposer();
        if (stick) scrollToBottom();
        loadMeta();
        return;
      }
    }

    async function send() {
      const text = input.value.trim();
      if (!text || running || !configured()) return;
      if (!chat) thread.innerHTML = "";
      thread.querySelectorAll(".chat-error, .chat-missing").forEach((node) => node.remove());
      thread.insertAdjacentHTML(
        "beforeend",
        `<div class="msg msg-user pending"><div class="msg-bubble">${esc(text)}</div></div>${streamHtml()}`,
      );
      scrollToBottom();
      input.value = "";
      autosize();
      running = true;
      updateComposer();
      let data;
      try {
        data = await api("chat_send", chatId, text);
      } catch (err) {
        if (!alive) return;
        running = false;
        renderThread();
        input.value = text;
        autosize();
        updateComposer();
        toast(err.message, { type: "error" });
        return;
      }
      if (!alive) return;
      if (chatId !== data.id) {
        history.replaceState(null, "", `#/chat/${data.id}`);
        S.route = { name: "chat", id: data.id };
      }
      applyChat(data);
      loadMeta();
    }

    async function stop() {
      if (!running || !chatId) return;
      sendButton.disabled = true;
      await attempt(api("chat_stop", chatId));
    }

    async function retry() {
      if (!chat || running) return;
      const data = await attempt(api("chat_retry", chat.id));
      if (data && alive) applyChat(data);
    }

    function renameChat() {
      if (!chat) return;
      const entry = openModal(
        `${modalHead("Переименовать чат")}
        <div class="modal-body"><div class="field" style="margin:0">
          <input class="input input-lg" id="chat-rename-input" type="text" maxlength="120" value="${esc(chat.title)}" autofocus>
        </div></div>
        <div class="modal-foot">
          <button type="button" class="btn" data-close>Отмена</button>
          <button type="button" class="btn btn-primary" id="chat-rename-save">Сохранить</button>
        </div>`,
        { size: "modal-small" },
      );
      const field = $("#chat-rename-input", entry.modal);
      setTimeout(() => field.select(), 40);
      const save = async () => {
        const title = field.value.trim();
        if (!title) return;
        const result = await attempt(api("chat_rename", chat.id, title));
        if (!result) return;
        entry.close();
        if (chat && chat.id === result.id) chat.title = result.title;
        renderHead();
        loadMeta();
      };
      $("#chat-rename-save", entry.modal).addEventListener("click", save);
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") save();
      });
    }

    async function deleteChat() {
      if (!chat) return;
      const ok = await confirmDialog({
        title: "Удалить чат?",
        text: `«${chat.title}» и вся переписка в нём будут удалены без возможности восстановления.`,
        confirm: "Удалить",
        danger: true,
      });
      if (!ok || !chat) return;
      const result = await attempt(api("chat_delete", chat.id), false);
      if (result === false) return;
      toast("Чат удалён", { type: "success" });
      if (location.hash === "#/chat") openChat(null);
      else location.hash = "#/chat";
      loadMeta();
    }

    list.addEventListener("click", (event) => {
      const item = event.target.closest("[data-chat]");
      if (item && item.dataset.chat !== chatId) location.hash = `#/chat/${item.dataset.chat}`;
    });
    $("#chat-new", root).addEventListener("click", () => {
      if (location.hash === "#/chat") openChat(null);
      else location.hash = "#/chat";
    });
    head.addEventListener("click", (event) => {
      const button = event.target.closest("#chat-menu");
      if (!button) return;
      openMenu(button, [
        { label: "Переименовать", icon: "edit", run: renameChat },
        "-",
        { label: "Удалить чат", icon: "trash", danger: true, run: deleteChat },
      ]);
    });
    thread.addEventListener("click", async (event) => {
      const suggestion = event.target.closest("[data-suggest]");
      if (suggestion) {
        input.value = suggestion.dataset.suggest;
        autosize();
        updateComposer();
        input.focus();
        return;
      }
      if (event.target.closest("[data-chat-retry]")) {
        retry();
        return;
      }
      const copy = event.target.closest("[data-copy]");
      if (copy && chat) {
        const message = chat.messages.find((item) => item.id === copy.dataset.copy);
        if (message) {
          const ok = await copyText(message.content);
          toast(ok ? "Скопировано" : "Не удалось скопировать", { type: ok ? "success" : "error", timeout: 2000 });
        }
      }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (running) stop();
      else send();
    });
    input.addEventListener("input", () => {
      autosize();
      updateComposer();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (!running) send();
    });

    if (meta) renderList();
    renderNote();
    openChat(chatId);
    loadMeta();

    return {
      update(next) {
        const id = next.id || null;
        if (id !== chatId) openChat(id);
        return true;
      },
      leave() {
        alive = false;
        pollToken += 1;
      },
    };
  }

  // ============================================================== settings
  function SettingsView(root) {
    root.innerHTML = `<div class="page"><div class="page-inner" style="max-width:820px">
      <div class="page-head"><div><h1 class="page-title">Настройки</h1>
        <p class="page-sub">Внешний вид, создание конспектов и обработка записей</p></div></div>
      <div class="settings-grid" id="settings-grid"><div class="state-panel"><span class="spinner spinner-lg"></span></div></div>
    </div></div>`;
    const grid = $("#settings-grid", root);
    let settings = null;
    let system = null;

    async function load() {
      try {
        settings = await api("get_settings");
        S.settings = settings;
      } catch (error) {
        grid.innerHTML = `<div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div>`;
        return;
      }
      render();
      loadChatGPTStatus().then(renderAccount);
      api("system_check")
        .then((result) => {
          system = result;
          renderSystem();
        })
        .catch(() => {});
    }

    function segmented(name, options, value) {
      return `<div class="segmented" data-pref="${name}">${options
        .map((option) => `<button type="button" data-value="${option.value}" class="${String(option.value) === String(value) ? "active" : ""}">${esc(option.label)}</button>`)
        .join("")}</div>`;
    }

    function render() {
      const o = settings.options;
      grid.innerHTML = `
        <section class="settings-card">
          <h2>${icon("theme")}Внешний вид</h2>
          <div class="setting"><div><div class="setting-title">Тема</div><div class="setting-hint">«Как в системе» следует настройке Windows/macOS.</div></div>
            <div class="setting-control">${segmented("theme", [{ value: "system", label: "Как в системе" }, { value: "light", label: "Светлая" }, { value: "dark", label: "Тёмная" }], S.prefs.theme)}</div></div>
          <div class="setting"><div><div class="setting-title">Шрифт конспекта</div><div class="setting-hint">Шрифт с засечками удобнее для долгого чтения.</div></div>
            <div class="setting-control">${segmented("reading_font", [{ value: "sans", label: "Без засечек" }, { value: "serif", label: "С засечками" }], S.prefs.reading_font)}</div></div>
          <div class="setting"><div><div class="setting-title">Размер текста</div><div class="setting-hint">Можно менять и в читалке кнопками А.</div></div>
            <div class="setting-control"><input type="range" id="font-scale" min="0.85" max="1.4" step="0.05" value="${S.prefs.font_scale || 1}" style="width:200px;accent-color:var(--accent)">
            <span class="faint small" id="font-scale-label" style="width:42px;text-align:right">${Math.round((S.prefs.font_scale || 1) * 100)}%</span></div></div>
        </section>

        <section class="settings-card">
          <h2>${icon("sparkles")}Создание конспектов</h2>
          <p>Конспект пишет языковая модель. Проще всего — через твой аккаунт ChatGPT; можно использовать API-ключ или любой веб-чат вручную.</p>
          <div class="setting"><div><div class="setting-title">Аккаунт ChatGPT</div><div class="setting-hint" id="account-hint">Проверяем…</div></div>
            <div class="setting-control" id="account-control"><span class="spinner"></span></div></div>
          <div class="setting"><div><div class="setting-title">Модель ChatGPT</div><div class="setting-hint">Например, gpt-5.5. Оставь как есть, если не уверен.</div></div>
            <div class="setting-control"><input class="input" id="s-chatgpt-model" value="${esc(settings.chatgpt_model)}" spellcheck="false"></div></div>
          <div class="setting"><div><div class="setting-title">API-провайдер</div><div class="setting-hint">Для конспектов и чата по API-ключу.</div></div>
            <div class="setting-control"><select class="select" id="s-provider">${o.providers
              .map((p) => `<option value="${p.id}" ${p.id === settings.api_provider ? "selected" : ""}>${esc(p.label)}</option>`)
              .join("")}</select></div></div>
          <div class="setting"><div><div class="setting-title">Модель API</div></div>
            <div class="setting-control"><input class="input" id="s-api-model" value="${esc(settings.api_model)}" spellcheck="false"></div></div>
          <div class="setting"><div><div class="setting-title">API-ключ</div><div class="setting-hint">Хранится в системном хранилище паролей, не в файлах.</div></div>
            <div class="setting-control"><input class="input" id="s-api-key" type="password" autocomplete="off" spellcheck="false"
              placeholder="${settings.api_key_set ? "Ключ сохранён — введи новый, чтобы заменить" : "sk-…"}"></div></div>
          <div class="card-foot">
            ${settings.api_key_set ? `<button type="button" class="btn btn-ghost" id="s-clear-key">Удалить ключ</button>` : ""}
            <button type="button" class="btn btn-primary" id="s-save-gen">Сохранить</button></div>
        </section>

        <section class="settings-card">
          <h2>${icon("cpu")}Подготовка лекций</h2>
          <p>Работает на этом компьютере. Точнее — медленнее.</p>
          <div class="setting"><div><div class="setting-title">Качество расшифровки</div><div class="setting-hint">tiny — быстро, base — оптимально, small — точнее, но в 2–3 раза дольше.</div></div>
            <div class="setting-control">${segmented("whisper_model", o.whisper_models.map((m) => ({ value: m, label: { tiny: "Быстро", base: "Оптимально", small: "Точно" }[m] || m })), settings.whisper_model)}</div></div>
          <div class="setting"><div><div class="setting-title">Кадры с экрана</div><div class="setting-hint">Как часто сохранять кадр презентации для распознавания и просмотра.</div></div>
            <div class="setting-control">${segmented("frame_interval_seconds", o.frame_intervals.map((s) => ({ value: s, label: `${s} с` })), settings.frame_interval_seconds)}</div></div>
          <div class="setting"><div><div class="setting-title">Распознавать текст на экране (OCR)</div><div class="setting-hint">Добавляет в конспект текст со слайдов и демонстрации экрана.</div></div>
            <div class="setting-control"><input type="checkbox" class="switch" id="s-ocr" ${settings.ocr_enabled ? "checked" : ""}></div></div>
        </section>

        <section class="settings-card">
          <h2>${icon("activity")}Проверка системы</h2>
          <div id="system-body"><div class="check-row"><span class="spinner"></span><span class="muted">Проверяем инструменты…</span></div></div>
          <div class="card-foot">
            <button type="button" class="btn btn-ghost" id="s-open-data">${icon("folder", "icon-sm")}Папка с данными</button>
            <button type="button" class="btn" id="s-recheck">${icon("refresh", "icon-sm")}Проверить снова</button></div>
        </section>
        <p class="faint small" style="text-align:center">Конспект ${esc(S.version)}</p>`;
      wire();
    }

    function renderAccount() {
      const status = S.chatgpt;
      const hint = $("#account-hint", grid);
      const control = $("#account-control", grid);
      if (!hint || !status) return;
      if (!status.available) {
        hint.textContent = "Нужен Codex CLI — установи его, чтобы входить через ChatGPT.";
        control.innerHTML = `<span class="pill pill-warning">Недоступно</span>`;
        return;
      }
      if (status.signed_in) {
        hint.textContent = status.plan ? `Подписка: ${status.plan}` : "Вход выполнен";
        const letter = (status.email || "C").trim()[0].toUpperCase();
        control.innerHTML = `<div class="account"><span class="avatar">${esc(letter)}</span><span>${esc(status.email || "ChatGPT")}</span></div>`;
        return;
      }
      hint.textContent = status.error || "Войди, чтобы создавать конспекты через подписку ChatGPT.";
      control.innerHTML = `<button type="button" class="btn btn-primary" id="s-login">${icon("user", "icon-sm")}Войти в ChatGPT</button>`;
      $("#s-login", grid).addEventListener("click", async (event) => {
        setBusy(event.currentTarget, true, "Ждём вход в окне…");
        try {
          S.chatgpt = await api("chatgpt_login");
          toast(S.chatgpt.signed_in ? "Вход выполнен" : "Вход не завершён", { type: S.chatgpt.signed_in ? "success" : "info" });
        } catch (error) {
          toast(error.message, { type: "error" });
        }
        renderAccount();
      });
    }

    function renderSystem() {
      const box = $("#system-body", grid);
      if (!box || !system) return;
      const row = (ok, title, hint, warn = false) => `<div class="check-row">
        <span class="status-dot ${ok ? "ok" : warn ? "warn" : "bad"}">${icon(ok ? "check" : warn ? "alert" : "x")}</span>
        <div class="grow"><div style="font-weight:550">${esc(title)}</div><div class="faint small">${esc(hint)}</div></div></div>`;
      box.innerHTML = [
        row(system.ffmpeg, "FFmpeg", system.ffmpeg ? "Обработка аудио и видео" : "Не найден — без него лекцию не подготовить. Запусти setup_local_ai.ps1."),
        row(
          system.tesseract && system.tesseract_rus,
          "Tesseract OCR",
          !system.tesseract
            ? "Не найден — текст со слайдов распознаваться не будет"
            : system.tesseract_rus
              ? "Распознавание текста, русский язык установлен"
              : "Нет русского языка — текст со слайдов распознаётся хуже",
          true,
        ),
        row(system.codex, "Codex CLI", system.codex ? "Нужен для входа через ChatGPT" : "Не найден — генерация через ChatGPT недоступна, но работают API и ручной режим", !system.codex),
        row(system.storage_ok, "Папка с данными", system.data_dir),
      ].join("");
    }

    function wire() {
      $$("[data-pref]", grid).forEach((group) =>
        group.addEventListener("click", async (event) => {
          const button = event.target.closest("[data-value]");
          if (!button) return;
          $$("button", group).forEach((item) => item.classList.toggle("active", item === button));
          const name = group.dataset.pref;
          const value = button.dataset.value;
          if (name === "theme" || name === "reading_font") {
            setPreference(name, value);
            return;
          }
          const payload = { [name]: name === "frame_interval_seconds" ? Number(value) : value };
          const result = await attempt(api("save_settings", payload));
          if (result) {
            settings = result;
            S.settings = result;
            toast("Сохранено", { type: "success", timeout: 1800 });
          }
        }),
      );
      const scale = $("#font-scale", grid);
      scale.addEventListener("input", () => {
        $("#font-scale-label", grid).textContent = `${Math.round(Number(scale.value) * 100)}%`;
        setPreference("font_scale", Number(scale.value));
      });
      $("#s-ocr", grid).addEventListener("change", async (event) => {
        const result = await attempt(api("save_settings", { ocr_enabled: event.target.checked }));
        if (result) {
          settings = result;
          S.settings = result;
          toast("Сохранено", { type: "success", timeout: 1800 });
        }
      });
      $("#s-provider", grid).addEventListener("change", (event) => {
        $("#s-api-model", grid).value = settings.options.default_models[event.target.value] || "";
      });
      const saveGeneration = async (clearKey = false) => {
        const key = $("#s-api-key", grid).value.trim();
        const payload = {
          chatgpt_model: $("#s-chatgpt-model", grid).value.trim(),
          api_provider: $("#s-provider", grid).value,
          api_model: $("#s-api-model", grid).value.trim(),
        };
        if (clearKey) {
          payload.api_key_changed = true;
          payload.api_key = "";
        } else if (key) {
          payload.api_key_changed = true;
          payload.api_key = key;
        }
        const result = await attempt(api("save_settings", payload));
        if (result) {
          settings = result;
          S.settings = result;
          toast("Настройки сохранены", { type: "success" });
          render();
          renderAccount();
          renderSystem();
        }
      };
      $("#s-save-gen", grid).addEventListener("click", () => saveGeneration(false));
      const clear = $("#s-clear-key", grid);
      if (clear) {
        clear.addEventListener("click", async () => {
          const ok = await confirmDialog({ title: "Удалить API-ключ?", text: "Генерация по API станет недоступна, пока не добавишь ключ снова.", confirm: "Удалить", danger: true });
          if (ok) saveGeneration(true);
        });
      }
      $("#s-open-data", grid).addEventListener("click", () => attempt(api("open_data_folder")));
      $("#s-recheck", grid).addEventListener("click", async (event) => {
        setBusy(event.currentTarget, true, "Проверяем…");
        system = await attempt(api("system_check"), system);
        await loadChatGPTStatus(true);
        setBusy(event.currentTarget, false);
        renderSystem();
        renderAccount();
      });
      renderAccount();
      renderSystem();
    }

    load();
    return {};
  }

  // ============================================================== keyboard
  document.addEventListener("keydown", (event) => {
    const ctrl = event.ctrlKey || event.metaKey;
    if (event.key === "Escape") {
      if (openMenuNode) {
        closeMenu();
        event.preventDefault();
        return;
      }
      const lightbox = $(".lightbox");
      if (lightbox) {
        lightbox.remove();
        return;
      }
      if (modalStack.length) {
        closeTopModal();
        event.preventDefault();
        return;
      }
      if (currentView && currentView.closeFind && currentView.closeFind()) return;
    }
    if (modalStack.length) return;
    if (ctrl && (event.key === "k" || event.key === "K" || event.code === "KeyK")) {
      event.preventDefault();
      if (S.route.name !== "library") location.hash = "#/library";
      setTimeout(() => currentView && currentView.focusSearch && currentView.focusSearch(), 30);
      return;
    }
    if (ctrl && (event.code === "KeyN")) {
      event.preventDefault();
      openAddModal();
      return;
    }
    if (ctrl && event.code === "KeyF" && currentView && currentView.openFind) {
      event.preventDefault();
      currentView.openFind();
      return;
    }
    if (isTyping(event.target) || ctrl || event.altKey) return;
    if (activeSession && document.contains($(".study", document)) && activeSession.key(event)) {
      event.preventDefault();
      return;
    }
    if (event.key === " " && Player.active && !activeSession) {
      event.preventDefault();
      Player.togglePlay();
    }
  });

  // ================================================================== boot
  $("#add-lecture").addEventListener("click", () => openAddModal());

  async function boot() {
    let data;
    try {
      data = await api("bootstrap");
    } catch (error) {
      $("#boot").innerHTML = `<div class="state-panel"><div class="notice notice-danger">${icon("alert")}<div>${esc(error.message)}</div></div></div>`;
      return;
    }
    S.prefs = { ...S.prefs, ...data.preferences };
    S.settings = data.settings;
    S.lectures = data.lectures;
    S.platform = data.platform;
    S.version = data.version;
    S.tasksVersion = data.tasks_version;
    S.libraryVersion = data.library_version;
    applyTasks(data.tasks);
    applyPreferences();
    $("#boot").remove();
    $("#app").hidden = false;
    renderDock();
    route();
    for (const warning of data.warnings || []) toast(warning, { type: "error", timeout: 10000 });
    window.__konspektReady = true;
    pollLoop();
    loadChatGPTStatus();
  }

  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot, { once: true });
})();
