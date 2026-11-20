/* Fin Bank: keep position and input values during form actions. */
(() => {
  "use strict";
  // Group digits while typing, but preserve the sign, fractional digits and caret.
  // Inputs are text rather than type=number because browsers reject grouping spaces.
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
      const count = old.slice(0, input.selectionStart)
        .replace(/\s/g, "").length;
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
  });
  prepareForms(document);

  document.addEventListener("click", (event) => {
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

  function popup(form, message, success=false) {
    const old = form.querySelector(".inline-error,.notice");
    if (old) old.remove();
    const node = document.createElement("p");
    node.className = success ? "notice" : "inline-error";
    node.setAttribute("role", success ? "status" : "alert");
    node.textContent = message;
    const button = form.querySelector('button[type="submit"],button:not([type])');
    if (button) button.insertAdjacentElement("afterend", node);
    else form.append(node);
  }
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest("main")) return;
    event.preventDefault();
    const top = window.scrollY;
    const page = document.querySelector("main");
    const opened = [...page.querySelectorAll("details")].flatMap(
      (el, index) => el.open ? [index] : []
    );
    const method = form.method.toUpperCase();
    const url = new URL(form.action, location.href);
    const data = new FormData(form);
    if (method === "GET") {
      for (const [key, value] of data) url.searchParams.set(key, value);
    } else {
      const field = page.querySelector('form[action="/bank"] input[name="as_of"]');
      if (field?.value) url.searchParams.set("as_of", field.value);
    }
    const button = form.querySelector('button[type="submit"],button:not([type])');
    if (button) button.disabled = true;
    let response;
    try {
      response = await fetch(url, {
        method, body: method === "GET" ? undefined : data,
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
    } catch (error) {
      console.error("Fin Bank request failed", error);
      popup(form, "Не удалось отправить запрос: " + (error?.message || String(error)));
      if (button) button.disabled = false;
      return;
    }
    try {
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!response.ok) {
        popup(form, doc.querySelector('[role="alert"]')?.textContent?.trim()
          || "Ошибка сохранения. Проверь значения.");
        return;
      }
      const next = doc.querySelector("main");
      if (!next) { popup(form, "Неправильный ответ локального сервера."); return; }
      // A successful POST may have already committed even if the UI refresh fails.
      // Keep the success response separate from any client-side rendering exception.
      page.replaceWith(next);
      prepareForms(next);
      const details = next.querySelectorAll("details");
      for (const index of opened) if (details[index]) details[index].open = true;
      if (method === "GET") history.replaceState(null, "", url.pathname + url.search);
      window.scrollTo({top, left: 0, behavior: "instant"});
      const matching = [...next.querySelectorAll("form")].find(
        candidate => new URL(candidate.action).pathname === new URL(form.action).pathname
      );
      if (matching) popup(matching, "Сохранено.", true);
    } catch (error) {
      console.error("Fin Bank response handling failed", error);
      const message = "Ответ сервера получен, но интерфейс не обновился: "
        + (error?.message || String(error))
        + ". Проверь запись перед повторной отправкой.";
      if (form.isConnected) popup(form, message);
      else {
        const alert = document.createElement("p");
        alert.className = "inline-error";
        alert.setAttribute("role", "alert");
        alert.textContent = message;
        document.querySelector("main")?.prepend(alert);
      }
    } finally {
      if (button) button.disabled = false;
    }
  });
})();
