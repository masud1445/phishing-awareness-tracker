/* Progressive enhancement only: all core forms and navigation work without JS. */
(() => {
  const toggle = document.querySelector(".nav-toggle");
  const backdrop = document.querySelector(".nav-backdrop");
  const navigation = document.getElementById("app-navigation");
  const workspace = document.querySelector(".app-workspace");
  const close = () => {
    document.body.classList.remove("nav-open");
    toggle?.setAttribute("aria-expanded", "false");
    if (backdrop) backdrop.hidden = true;
    if (navigation) navigation.inert = innerWidth <= 800;
    if (workspace) workspace.inert = false;
  };
  toggle?.addEventListener("click", () => {
    const opened = document.body.classList.toggle("nav-open");
    toggle.setAttribute("aria-expanded", String(opened));
    backdrop.hidden = !opened;
    navigation.inert = !opened;
    workspace.inert = opened;
    if (opened) navigation.querySelector("a")?.focus();
  });
  backdrop?.addEventListener("click", () => {
    close();
    toggle.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      document.body.classList.contains("nav-open")
    ) {
      close();
      toggle.focus();
    }
  });
  close();
  navigation?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !document.body.classList.contains("nav-open"))
      return;
    const focusable = Array.from(navigation.querySelectorAll("a,button"));
    const first = focusable[0],
      last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  const media = matchMedia("(min-width:801px)");
  media.addEventListener("change", () => close());
  document.querySelectorAll("[data-filter-scope]").forEach((scope) => {
    const search = scope.querySelector("[data-search]"),
      status = scope.querySelector("[data-status-filter]");
    const rows = Array.from(scope.querySelectorAll("[data-filter-row]"));
    const update = () => {
      let count = 0;
      rows.forEach((row) => {
        const visible =
          (!search?.value ||
            row.textContent
              .toLowerCase()
              .includes(search.value.toLowerCase())) &&
          (!status?.value || row.dataset.status === status.value);
        row.hidden = !visible;
        if (visible) count++;
      });
      const output = scope.querySelector("[data-filter-count]");
      if (output) output.textContent = `${count} of ${rows.length} shown`;
      const empty = scope.querySelector(".no-filter-results");
      if (empty) empty.hidden = count !== 0;
    };
    search?.addEventListener("input", update);
    status?.addEventListener("change", update);
    update();
  });
  const body = document.getElementById("body"),
    subject = document.getElementById("subject");
  let insertionTarget = body;
  [body, subject].forEach((field) =>
    field?.addEventListener("focus", () => (insertionTarget = field)),
  );
  document.querySelectorAll("[data-token]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!insertionTarget) return;
      insertionTarget.setRangeText(
        button.dataset.token,
        insertionTarget.selectionStart,
        insertionTarget.selectionEnd,
        "end",
      );
      insertionTarget.focus();
      insertionTarget.dispatchEvent(new Event("input", { bubbles: true }));
      document.getElementById("ui-feedback").textContent =
        "Personalization token inserted.";
    }),
  );
  document.querySelectorAll("[data-format]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!body) return;
      const fragments = {
        heading: "\n## Section heading\n",
        paragraph: "\n\nNew paragraph\n",
        divider: "\n---\n",
        section: "\n## Content section\nAdd your message here.\n",
      };
      body.setRangeText(
        fragments[button.dataset.format] || "",
        body.selectionStart,
        body.selectionEnd,
        "end",
      );
      const layout = document.getElementById("content_style");
      if (layout && button.dataset.format !== "paragraph")
        layout.value = "sectioned";
      body.focus();
      body.dispatchEvent(new Event("input", { bubbles: true }));
    }),
  );
  const loading = document.querySelector(".loading-bar");
  document.querySelectorAll("form").forEach((form) =>
    form.addEventListener("submit", () => {
      if (form.checkValidity()) {
        if (loading) loading.hidden = false;
        form.setAttribute("aria-busy", "true");
      }
    }),
  );
  window.addEventListener("pageshow", () => {
    if (loading) loading.hidden = true;
    document
      .querySelectorAll("form[aria-busy]")
      .forEach((form) => form.removeAttribute("aria-busy"));
  });
  document.querySelectorAll("[data-confirm-delete]").forEach((form) =>
    form.addEventListener("submit", (event) => {
      if (
        !window.confirm(
          "Delete this sender account? Campaign history will remain readable, but this account will no longer deliver email.",
        )
      ) {
        event.preventDefault();
        if (loading) loading.hidden = true;
        form.removeAttribute("aria-busy");
      }
    }),
  );
})();
