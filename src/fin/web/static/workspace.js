/* Task-first Fin workspace: actions reuse audited /bank endpoints and CSRF. */
(() => {
  "use strict";

  const dialog = document.getElementById("workspace-dialog");
  const dialogBody = document.getElementById("workspace-dialog-body");
  const dialogTitle = document.getElementById("workspace-dialog-title");
  const dateField = document.getElementById("workspace-date");
  let busy = false;
  let restoreFocus = null;

  function showMessage(message, error = false) {
    const toast = document.getElementById("workspace-toast");
    toast.textContent = message;
    toast.style.background = error ? "#923f4b" : "#1d4d60";
    // An error remains visible until the user interacts, rather than vanishing.
    if (!error) toast.dataset.success = "true";
    else delete toast.dataset.success;
  }

  function openDialog(title, node, trigger) {
    restoreFocus = trigger || document.activeElement;
    dialogTitle.textContent = title;
    dialogBody.replaceChildren(node);
    if (!dialog.open) dialog.showModal();
    (dialogBody.querySelector("input:not([type=hidden]),select,button") ||
      dialog.querySelector("[data-close-dialog]"))?.focus({preventScroll: true});
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
    dialogBody.replaceChildren();
    if (restoreFocus?.isConnected) restoreFocus.focus({preventScroll: true});
    restoreFocus = null;
  }

  function asOfURL(path) {
    const url = new URL(path, location.origin);
    if (!url.searchParams.has("as_of")) {
      url.searchParams.set("as_of", dateField.value);
    }
    return url;
  }

  async function loadBankDocument() {
    const response = await fetch(asOfURL("/bank/forms"), {credentials: "same-origin"});
    if (!response.ok) throw new Error("Не удалось открыть текущие формы Fin");
    return new DOMParser().parseFromString(await response.text(), "text/html");
  }

  function matchingForm(documentRoot, path, id, fieldName = "id",
                        extraField = null, extraValue = null) {
    return [...documentRoot.querySelectorAll("form")].find((form) => {
      if (new URL(form.action, location.origin).pathname !== path) return false;
      const matches = (name, value) =>
        [...form.querySelectorAll("input")].some((node) =>
          node.name === name && node.value === value);
      return (!id || matches(fieldName, id)) &&
        (!extraField || matches(extraField, extraValue));
    });
  }

  // Preserve precise decimal values while grouping thousands only in the UI.
  function groupDigits(value) {
    const plain = value.replace(/\s/g, "");
    const parts = /^(-?)(\d+)([.,]\d*)?$/.exec(plain);
    if (!parts) return value;
    return parts[1] + parts[2].replace(/\B(?=(\d{3})+(?!\d))/g, " ") +
      (parts[3] || "");
  }

  function formatNumber(input) {
    const old = input.value;
    const grouped = groupDigits(old);
    if (old === grouped) return;
    let caret = null;
    if (document.activeElement === input && input.selectionStart !== null) {
      const count = old.slice(0, input.selectionStart).replace(/\s/g, "").length;
      let seen = 0;
      let position = 0;
      while (position < grouped.length && seen < count) {
        if (!/\s/.test(grouped[position])) seen++;
        position++;
      }
      caret = position;
    }
    input.value = grouped;
    if (caret !== null) input.setSelectionRange(caret, caret);
  }

  function updatePrincipal(form) {
    const summary = form?.querySelector("[data-auto-principal]");
    if (!summary) return;
    const bond = summary.dataset.investment === "bond";
    const read = (name) => form.querySelector('input[name="' + name + '"]')?.value || "";
    summary.querySelector("[data-auto-date]").textContent =
      read("matures_on") || "дата погашения";
    const nominal = read(bond ? "face_value" : "principal")
      .replace(/\s/g, "").replace(",", ".");
    const quantity = bond ? read("quantity").replace(/\s/g, "") : "1";
    const match = /^(\d+)(?:\.(\d+))?$/.exec(nominal);
    const destination = summary.querySelector("[data-auto-amount]");
    if (!match || !/^\d+$/.test(quantity) || !BigInt(quantity)) {
      destination.textContent = bond ? "номинал × количество" : "тело депозита";
      return;
    }
    const precision = match[2]?.length || 0;
    const multiplied = BigInt(match[1] + (match[2] || "")) * BigInt(quantity);
    const digits = multiplied.toString().padStart(precision + 1, "0");
    destination.textContent = groupDigits(precision
      ? digits.slice(0, -precision) + "." + digits.slice(-precision)
      : digits);
  }

  function refreshFormFields(root) {
    for (const select of root.querySelectorAll('select[name="bank"],select[name="custodian"]')) {
      const field = select.closest("form")?.querySelector('input[name="' +
        (select.name === "bank" ? "bank_other" : "custodian_other") + '"]');
      if (field) {
        const custom = select.value === "__other__";
        field.required = custom;
        field.closest("label").hidden = !custom;
      }
    }
  }

  async function formFromLegacy(trigger) {
    const source = await loadBankDocument();
    let form = null;
    if (trigger.dataset.formId) {
      form = source.getElementById(trigger.dataset.formId)?.querySelector("form");
    } else if (trigger.dataset.editAction) {
      form = matchingForm(source, trigger.dataset.editAction, trigger.dataset.recordId,
        trigger.dataset.matchField || "id",
        trigger.dataset.matchExtraField, trigger.dataset.matchExtraValue);
    }
    if (!form) {
      showMessage("Это действие пока недоступно. Открой прежний интерфейс Fin.", true);
      return;
    }
    const cloned = form.cloneNode(true);
    openDialog(trigger.textContent.trim(), cloned, trigger);
    refreshFormFields(cloned);
    cloned.querySelectorAll("input[data-grouped]").forEach(formatNumber);
    updatePrincipal(cloned);
  }

  function confirmPreview(source, trigger) {
    const section = source.querySelector("#confirm-deletion");
    if (!section) {
      const error = source.querySelector('[role="alert"]');
      showMessage(error?.textContent?.trim() || "Не удалось подготовить удаление.", true);
      return;
    }
    openDialog("Предварительный просмотр удаления", section.cloneNode(true), trigger);
  }

  function csrf() {
    // The server renders the token into this page; no cookies or storage.
    return document.querySelector('meta[name="fin-csrf"]')?.content || "";
  }

  async function previewDelete(trigger) {
    const body = new FormData();
    body.set("kind", trigger.dataset.deleteKind);
    body.set("identifier", trigger.dataset.recordId);
    body.set("csrf", csrf());
    const response = await fetch(asOfURL("/bank/delete/preview"), {
      method: "POST", body, credentials: "same-origin"
    });
    const source = new DOMParser().parseFromString(await response.text(), "text/html");
    if (!response.ok) {
      showMessage(source.querySelector('[role="alert"]')?.textContent?.trim()
        || "Удаление невозможно: проверь связанные операции.", true);
      return;
    }
    confirmPreview(source, trigger);
  }

  function importPreview(source) {
    const confirmation = matchingForm(source, "/bank/securities/confirm");
    if (!confirmation) {
      const msg = source.querySelector('[role="alert"]');
      showMessage(msg?.textContent?.trim() || "PDF не распознан или содержит ошибки.", true);
      return;
    }
    const parent = source.getElementById("imported-securities");
    const wrapper = document.createElement("div");
    const heading = document.createElement("h3");
    heading.textContent = "Проверь распознанные записи перед импортом";
    wrapper.append(heading);
    const rows = parent?.querySelector("h3 + .scroll");
    if (rows) wrapper.append(rows.cloneNode(true));
    const note = document.createElement("p");
    note.className = "small";
    note.textContent = "Подтверждение сохранит нормализованные данные локально; PDF не попадёт в Git.";
    wrapper.append(note, confirmation.cloneNode(true));
    openDialog("Импорт выписки", wrapper, restoreFocus);
  }

  async function refreshCurrentWorkspace(clearSelection) {
    const url = new URL(location.href);
    if (clearSelection) url.searchParams.delete("target");
    const response = await fetch(url, {credentials: "same-origin"});
    if (!response.ok) {
      location.assign(url.toString());
      return;
    }
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const currentMain = document.getElementById("workspace-main");
    const incomingMain = doc.getElementById("workspace-main");
    if (!currentMain || !incomingMain) {
      location.assign(url.toString());
      return;
    }
    // Preserve the toolbar, date picker and sidebar instead of replacing the page.
    [...currentMain.children].filter((node) => !node.classList.contains("main-toolbar"))
      .forEach((node) => node.remove());
    [...incomingMain.children].filter((node) => !node.classList.contains("main-toolbar"))
      .forEach((node) => currentMain.append(node));
    if (clearSelection) history.replaceState(null, "", url);
    const search = document.getElementById("object-search");
    if (search) search.value = "";
  }

  async function submitAction(form) {
    const url = asOfURL(form.action);
    const response = await fetch(url, {
      method: form.method.toUpperCase(), body: new FormData(form),
      credentials: "same-origin", headers: {"X-Requested-With": "XMLHttpRequest"}
    });
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    if (!response.ok) {
      const error = doc.querySelector('[role="alert"]');
      showMessage(error?.textContent?.trim() || "Ошибка сохранения. Проверь введённые данные.", true);
      return;
    }
    if (url.pathname === "/bank/securities/preview") {
      importPreview(doc);
      return;
    }
    const isDeletion = url.pathname === "/bank/delete/confirm";
    closeDialog();
    await refreshCurrentWorkspace(isDeletion);
    showMessage(isDeletion ? "Удалено. Расчёты пересчитаны." : "Сохранено.");
  }

  document.addEventListener("click", async (event) => {
    const close = event.target.closest?.("[data-close-dialog]");
    if (close) {event.preventDefault();closeDialog();return;}
    const trigger = event.target.closest?.("[data-form-id],[data-edit-action],[data-delete-kind]");
    if (!trigger) return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    try {
      if (trigger.dataset.deleteKind) await previewDelete(trigger);
      else await formFromLegacy(trigger);
    } catch (error) {
      console.error("Fin workspace action failed", error);
      showMessage("Не удалось загрузить форму. Старый интерфейс доступен по ссылке сверху.", true);
    } finally {busy = false;}
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest("#workspace-dialog")) return;
    event.preventDefault();
    if (busy) return;
    if (!form.reportValidity()) return;
    busy = true;
    const button = event.submitter || form.querySelector('button[type="submit"],button:not([type])');
    if (button) button.disabled = true;
    try {await submitAction(form);}
    catch (error) {
      console.error("Fin workspace submission failed", error);
      showMessage("Связь с локальным сервером потеряна. Проверь результат перед повтором.", true);
    } finally {
      busy = false;
      if (button?.isConnected) button.disabled = false;
    }
  });

  document.addEventListener("click", (event) => {
    const add = event.target.closest?.("[data-schedule-add]");
    if (add && add.closest("#workspace-dialog")) {
      const builder = add.closest("[data-schedule-builder]");
      const template = builder?.querySelector("template[data-schedule-template]");
      const rows = builder?.querySelector("[data-schedule-rows]");
      if (template && rows) {
        rows.append(template.content.cloneNode(true));
        rows.querySelectorAll("input[data-grouped]").forEach(formatNumber);
      }
    }
    const remove = event.target.closest?.("[data-schedule-remove]");
    if (remove && remove.closest("#workspace-dialog")) {
      remove.closest("[data-schedule-row]")?.remove();
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches?.('select[name="bank"],select[name="custodian"]')) {
      refreshFormFields(event.target.closest("form"));
    }
  });

  document.addEventListener("input", (event) => {
    const search = event.target;
    if (search.matches?.("input[data-grouped]")) formatNumber(search);
    if (search.matches?.('input[name="matures_on"],input[name="quantity"],input[name="face_value"],input[name="principal"]')) {
      updatePrincipal(search.closest("form"));
    }
    if (search.id === "object-search") {
      const query = search.value.trim().toLocaleLowerCase("ru");
      let visible = 0;
      for (const link of document.querySelectorAll("#object-list .object-row")) {
        const found = link.textContent.toLocaleLowerCase("ru").includes(query);
        link.hidden = !found;
        if (found) visible++;
      }
      for (const label of document.querySelectorAll("#object-list .list-group-label")) {
        label.hidden = Boolean(query);
      }
      search.setAttribute("aria-label", "Поиск: " + visible + " объектов");
    }
  });

  dialog?.addEventListener("close", () => {
    dialogBody.replaceChildren();
    if (restoreFocus?.isConnected) restoreFocus.focus({preventScroll: true});
    restoreFocus = null;
  });
})();
