/* Fin Bank: keep position and input values during form actions. */
(() => {
  "use strict";
  function popup(form, message, success=false) {
    const old = form.querySelector(".inline-error,.notice");
    if (old) old.remove();
    const node = document.createElement("p");
    node.className = success ? "notice" : "inline-error";
    node.setAttribute("role", success ? "status" : "alert");
    node.textContent = message;
    const button = form.querySelector("button");
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
    const button = form.querySelector("button");
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
