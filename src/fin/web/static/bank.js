/* Fin Bank: small workspaces, stable period controls, native confirmation dialog. */
(() => {
  "use strict";

  const PANELS = {
    overview: ["cash-outlook"],
    accounts: ["accounts"],
    investments: ["holdings", "imported-securities"],
    payments: ["payments"],
    reconciliation: ["monthly-controls"],
    transactions: ["events"]
  };
  const PANEL_TO_VIEW = {
    overview: "overview", "cash-outlook": "overview", accounts: "accounts",
    holdings: "investments", "imported-securities": "investments",
    payments: "payments", "monthly-controls": "reconciliation",
    events: "transactions"
  };
  const NOTES = {
    overview: "Дата меняет только расчёт. Реальные деньги не включают будущие купоны и погашения.",
    accounts: "Банковские счета и наличные показываются отдельно. Переводы между ними учитываются в реальных остатках.",
    investments: "Подтверждённые позиции из выписок и ручные лоты не суммируются дважды.",
    payments: "Плановые выплаты, фактические поступления и зачисления учитываются отдельно.",
    reconciliation: "Месячные снимки ничего не проводят автоматически. Корректировка требует подтверждения.",
    transactions: "В истории показаны реальные проводки. Изменения влияют на остатки с даты операции."
  };
  let activeView = "overview";
  let notificationTimer = null;
  let lastDialogTrigger = null;
  let inFlight = false;

  function groupDigits(value) {
    const plain = value.replace(/\s/g, "");
    const parts = /^(-?)(\d+)([.,]\d*)?$/.exec(plain);
    if (!parts) return value;
    const digits = parts[2].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return parts[1] + digits + (parts[3] || "");
  }

  function formatNumber(input) {
    const old = input.value;
    const grouped = groupDigits(old);
    if (old === grouped) return;
    let caret = null;
    if (document.activeElement === input && input.selectionStart !== null) {
      const count = old.slice(0, input.selectionStart).replace(/\s/g, "").length;
      let seen = 0;
      let at = 0;
      while (at < grouped.length && seen < count) {
        if (!/\s/.test(grouped[at])) seen++;
        at++;
      }
      caret = at;
    }
    input.value = grouped;
    if (caret !== null) input.setSelectionRange(caret, caret);
  }

  function updateBankChoice(select) {
    const otherField = select.name === "custodian" ? "custodian_other" : "bank_other";
    const field = select.closest("form")?.querySelector('input[name="' + otherField + '"]');
    if (!field) return;
    const custom = select.value === "__other__";
    field.required = custom;
    field.closest("label").style.display = custom ? "" : "none";
  }

  function updatePrincipal(form) {
    if (!form) return;
    const summary = form.querySelector("[data-auto-principal]");
    if (!summary) return;
    const isBond = summary.dataset.investment === "bond";
    const read = (field) => form.querySelector('input[name="' + field + '"]')?.value || "";
    const day = read("matures_on");
    summary.querySelector("[data-auto-date]").textContent = day || "дата погашения";
    const nominal = (read(isBond ? "face_value" : "principal"))
      .replace(/\s/g, "").replace(",", ".");
    const quantity = isBond ? read("quantity").replace(/\s/g, "") : "1";
    const match = /^(\d+)(?:\.(\d+))?$/.exec(nominal);
    const amount = summary.querySelector("[data-auto-amount]");
    if (!match || !/^\d+$/.test(quantity) || !BigInt(quantity)) {
      amount.textContent = isBond ? "номинал × количество" : "тело депозита";
      return;
    }
    const precision = match[2]?.length || 0;
    const number = BigInt(match[1] + (match[2] || "")) * BigInt(quantity);
    const digits = number.toString().padStart(precision + 1, "0");
    const exact = precision
      ? digits.slice(0, -precision) + "." + digits.slice(-precision)
      : digits;
    amount.textContent = groupDigits(exact);
  }

  function prepareForms(root) {
    root.querySelectorAll("input[data-grouped]").forEach(formatNumber);
    root.querySelectorAll('select[name="bank"],select[name="custodian"]')
      .forEach(updateBankChoice);
    root.querySelectorAll("form").forEach(updatePrincipal);
  }

  function viewFromHash(hash) {
    return PANEL_TO_VIEW[(hash || "").replace(/^#/, "")] || "overview";
  }

  function activateView(view, updateHistory = false) {
    const safe = Object.hasOwn(PANELS, view) ? view : "overview";
    activeView = safe;
    const visible = new Set(PANELS[safe]);
    for (const id of new Set(Object.values(PANELS).flat())) {
      const section = document.getElementById(id);
      if (section) section.hidden = !visible.has(id);
    }
    document.querySelectorAll("#workspace-nav [data-view]").forEach((tab) => {
      const selected = tab.dataset.view === safe;
      if (selected) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
    const description = document.getElementById("workspace-note");
    if (description) description.textContent = NOTES[safe];
    if (updateHistory) {
      history.replaceState(null, "", location.pathname + location.search + "#" + safe);
    }
  }

  function notify(message, error = false) {
    const target = document.getElementById("fin-toast");
    if (!target) return;
    if (notificationTimer) clearTimeout(notificationTimer);
    target.replaceChildren();
    const node = document.createElement("p");
    node.className = error ? "inline-error" : "notice";
    node.setAttribute("role", error ? "alert" : "status");
    node.textContent = message;
    target.append(node);
    notificationTimer = setTimeout(() => target.replaceChildren(), error ? 8000 : 3200);
  }

  function closeDialog() {
    const dialog = document.getElementById("fin-dialog");
    if (dialog?.open) dialog.close();
    document.getElementById("dialog-body")?.replaceChildren();
    if (lastDialogTrigger?.isConnected) lastDialogTrigger.focus({preventScroll: true});
    lastDialogTrigger = null;
  }

  function showDeleteDialog(section) {
    const dialog = document.getElementById("fin-dialog");
    const target = document.getElementById("dialog-body");
    if (!dialog || !target) return;
    target.replaceChildren(section);
    if (!dialog.open) dialog.showModal();
    const confirmation = dialog.querySelector('input[name="confirmation"]');
    (confirmation || dialog.querySelector("[data-close-dialog]"))?.focus({preventScroll: true});
  }

  // Works both for native full-page responses and AJAX previews; without JS
  // the preview remains an ordinary inline section so deletion is still possible.
  const existingPreview = document.querySelector("main #confirm-deletion");
  if (existingPreview) showDeleteDialog(existingPreview);

  function isoUTC(day) {
    return day.toISOString().slice(0, 10);
  }

  function shiftedMonth(value, shift) {
    const [year, month, day] = value.split("-").map(Number);
    if (!year || !month || !day) return value;
    const start = new Date(Date.UTC(year, month - 1 + shift, 1));
    const lastDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0))
      .getUTCDate();
    start.setUTCDate(Math.min(day, lastDay));
    return isoUTC(start);
  }

  function previousMonthEnd(value) {
    const [year, month] = value.split("-").map(Number);
    if (!year || !month) return value;
    return isoUTC(new Date(Date.UTC(year, month - 1, 0)));
  }

  function applyDate(value) {
    const field = document.getElementById("as-of-field");
    const form = document.getElementById("as-of-form");
    if (!field || !form || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
    field.value = value;
    form.requestSubmit();
  }

  function rememberExpanded() {
    return new Set(
      [...document.querySelectorAll("main details[id][open]")].map((item) => item.id)
    );
  }

  function updateVisibleData(next, selectedDate, isDateUpdate) {
    // Only swap data sections. Date form, navigation, focus and their dimensions
    // are NOT destroyed, unlike replacing the entire <main>.
    const opened = rememberExpanded();
    const visibleId = PANELS[activeView][0];
    const original = document.getElementById(visibleId);
    const oldTop = original?.getBoundingClientRect().top;
    prepareForms(next);
    const notices = document.getElementById("page-messages");
    const updatedNotices = next.querySelector("#page-messages");
    if (notices && updatedNotices) notices.replaceChildren(...updatedNotices.childNodes);
    const main = document.getElementById("bank-main");
    for (const id of new Set(Object.values(PANELS).flat())) {
      const incoming = next.querySelector("#" + id);
      const existing = document.getElementById(id);
      if (existing && incoming) existing.replaceWith(incoming);
      else if (!existing && incoming) main.insertBefore(incoming, main.querySelector("footer"));
      else if (existing && !incoming) existing.remove();
    }
    for (const id of opened) {
      const item = document.getElementById(id);
      if (item?.tagName === "DETAILS") item.open = true;
    }
    const dateField = document.getElementById("as-of-field");
    if (dateField && selectedDate) {
      dateField.value = selectedDate;
      const label = document.getElementById("selected-date-label");
      if (label) label.textContent = selectedDate.split("-").reverse().join(".");
    }
    activateView(activeView);
    const updated = document.getElementById(visibleId);
    if (oldTop !== undefined && updated) {
      const delta = updated.getBoundingClientRect().top - oldTop;
      if (Math.abs(delta) > 1) {
        // Correct for content height changes while preserving the same visual anchor.
        window.scrollTo({top: Math.max(0, window.scrollY + delta), behavior: "instant"});
      }
    }
    if (isDateUpdate && dateField) {
      const hint = document.getElementById("workspace-note");
      if (hint) hint.textContent = NOTES[activeView];
    }
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches?.("input[data-grouped]")) formatNumber(event.target);
    if (event.target.matches?.('input[name="matures_on"],input[name="quantity"],input[name="face_value"],input[name="principal"]')) {
      updatePrincipal(event.target.closest("form"));
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.matches?.('select[name="bank"],select[name="custodian"]')) {
      updateBankChoice(event.target);
    }
    if (event.target.id === "as-of-field") {
      event.target.closest("form")?.requestSubmit();
    }
  });

  document.addEventListener("click", (event) => {
    const tab = event.target.closest?.("#workspace-nav [data-view]");
    if (tab) {
      event.preventDefault();
      activateView(tab.dataset.view, true);
      return;
    }

    const jump = event.target.closest?.("[data-switch-view]");
    if (jump) {
      event.preventDefault();
      activateView(jump.dataset.switchView, true);
      const target = document.getElementById(jump.dataset.focusId);
      if (target?.tagName === "DETAILS") target.open = true;
      target?.scrollIntoView({behavior: "instant", block: "nearest"});
      return;
    }

    const inPage = event.target.closest?.('a[href^="#"]');
    if (inPage && PANEL_TO_VIEW[inPage.hash.slice(1)]) {
      event.preventDefault();
      activateView(PANEL_TO_VIEW[inPage.hash.slice(1)], true);
      return;
    }

    if (event.target.closest?.("[data-close-dialog]") || event.target.closest?.('#fin-dialog a[href^="/bank"]')) {
      event.preventDefault();
      closeDialog();
      return;
    }

    const preset = event.target.closest?.("[data-date-preset]");
    if (preset) {
      const field = document.getElementById("as-of-field");
      if (field) {
        applyDate(preset.dataset.datePreset === "today"
          ? field.dataset.today : previousMonthEnd(field.dataset.today));
      }
      return;
    }
    const step = event.target.closest?.("[data-month-step]");
    if (step) {
      const field = document.getElementById("as-of-field");
      if (field) applyDate(shiftedMonth(field.value, Number(step.dataset.monthStep)));
      return;
    }

    const add = event.target.closest?.("[data-schedule-add]");
    if (add) {
      const builder = add.closest("[data-schedule-builder]");
      const template = builder.querySelector("template[data-schedule-template]");
      const row = template.content.firstElementChild.cloneNode(true);
      builder.querySelector("[data-schedule-rows]").append(row);
      prepareForms(row);
      return;
    }
    const remove = event.target.closest?.("[data-schedule-remove]");
    if (remove) remove.closest("[data-schedule-row]")?.remove();
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest("main, #fin-dialog")) return;
    event.preventDefault();
    if (inFlight) {
      notify("Предыдущий запрос ещё выполняется.", true);
      return;
    }

    const method = form.method.toUpperCase();
    const url = new URL(form.action, location.href);
    const data = new FormData(form);
    const dateField = document.getElementById("as-of-field");
    if (method === "GET") {
      for (const [key, value] of data) url.searchParams.set(key, value);
    } else if (dateField?.value && !url.searchParams.has("as_of")) {
      url.searchParams.set("as_of", dateField.value);
    }
    const isDateUpdate = form.id === "as-of-form";
    const isDeletePreview = url.pathname === "/bank/delete/preview";
    const isDeleteConfirm = url.pathname === "/bank/delete/confirm";
    const button = event.submitter || form.querySelector('button[type="submit"],button:not([type])');
    if (isDeletePreview) lastDialogTrigger = button;
    if (button) button.disabled = true;
    inFlight = true;
    try {
      const response = await fetch(url, {
        method,
        body: method === "GET" ? undefined : data,
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!response.ok) {
        notify(doc.querySelector('[role="alert"]')?.textContent?.trim()
          || "Ошибка сохранения. Проверь введённые значения.", true);
        return;
      }
      if (isDeletePreview) {
        const preview = doc.querySelector("#confirm-deletion");
        if (!preview) {
          notify("Сервер не вернул предварительный просмотр удаления.", true);
          return;
        }
        showDeleteDialog(preview);
        return;
      }

      const next = doc.querySelector("main");
      if (!next) {
        notify("Локальный сервер вернул неподдерживаемый ответ.", true);
        return;
      }
      if (isDeleteConfirm) closeDialog();
      const finalURL = new URL(response.url);
      const selectedDate = isDateUpdate
        ? url.searchParams.get("as_of")
        : (finalURL.searchParams.get("as_of") || dateField?.value);
      const redirectHash = finalURL.hash.slice(1);
      if (!isDateUpdate && PANEL_TO_VIEW[redirectHash]) {
        activeView = PANEL_TO_VIEW[redirectHash];
      }
      updateVisibleData(next, selectedDate, isDateUpdate);
      if (isDateUpdate) {
        history.replaceState(null, "", "/bank?as_of=" + encodeURIComponent(selectedDate)
          + "#" + activeView);
      }
      if (!isDateUpdate) notify(isDeleteConfirm ? "Удалено. Остатки пересчитаны." : "Сохранено.");
    } catch (error) {
      console.error("Fin Bank request failed", error);
      notify("Нет связи с локальным сервером. Проверь запись перед повторной отправкой.", true);
    } finally {
      inFlight = false;
      if (button?.isConnected) button.disabled = false;
    }
  });

  document.getElementById("fin-dialog")?.addEventListener("close", () => {
    document.getElementById("dialog-body")?.replaceChildren();
    if (lastDialogTrigger?.isConnected) lastDialogTrigger.focus({preventScroll: true});
    lastDialogTrigger = null;
  });
  window.addEventListener("hashchange", () => activateView(viewFromHash(location.hash)));
  prepareForms(document);
  activateView(viewFromHash(location.hash));
})();
