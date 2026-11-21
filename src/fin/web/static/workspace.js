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
    const response = await fetch(asOfURL("/bank"), {credentials: "same-origin"});
    if (!response.ok) throw new Error("Не удалось открыть текущие формы Fin");
    return new DOMParser().parseFromString(await response.text(), "text/html");
  }

  function matchingForm(documentRoot, path, id, fieldName = "id") {
    return [...documentRoot.querySelectorAll("form")].find((form) => {
      if (new URL(form.action, location.origin).pathname !== path) return false;
      if (!id) return true;
      const input = [...form.querySelectorAll("input")].find((node) => node.name === fieldName);
      return input?.value === id;
    });
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
        trigger.dataset.matchField || "id");
    }
    if (!form) {
      showMessage("Это действие пока недоступно. Открой прежний интерфейс Fin.", true);
      return;
    }
    const cloned = form.cloneNode(true);
    openDialog(trigger.textContent.trim(), cloned, trigger);
    refreshFormFields(cloned);
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
      if (template && rows) rows.append(template.content.cloneNode(true));
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
