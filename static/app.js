document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("[data-sidebar]");
  document.querySelectorAll("[data-menu-toggle]").forEach((button) => {
    button.addEventListener("click", () => sidebar?.classList.toggle("open"));
  });

  document.querySelectorAll("[data-dismiss]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".toast")?.remove());
    window.setTimeout(() => button.closest(".toast")?.remove(), 6500);
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-dialog-open]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.dialogOpen)?.showModal());
  });
  document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.querySelectorAll("[data-auto-upload]").forEach((input) => {
    input.addEventListener("change", () => input.files?.length && input.form.requestSubmit());
  });

  const zone = document.querySelector("[data-drop-zone]");
  const dropInput = document.querySelector("[data-drop-input]");
  if (zone && dropInput) {
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("dragover");
    }));
    zone.addEventListener("drop", (event) => {
      if (!event.dataTransfer.files.length) return;
      dropInput.files = event.dataTransfer.files;
      dropInput.form.requestSubmit();
    });
    dropInput.addEventListener("change", () => dropInput.files?.length && dropInput.form.requestSubmit());
  }

  document.querySelectorAll("[data-selection-form]").forEach((form) => {
    const minimum = Number(form.dataset.min || 1);
    const maximum = Number(form.dataset.max || Infinity);
    const inputs = [...form.querySelectorAll('input[name="document_ids"]')];
    const button = form.querySelector("[data-submit-button]");
    const counter = form.querySelector("[data-selected-count]");
    const update = () => {
      const selected = inputs.filter((input) => input.checked).length;
      if (counter) counter.textContent = String(selected);
      if (button) button.disabled = selected < minimum || selected > maximum;
    };
    inputs.forEach((input) => input.addEventListener("change", update));
    update();
  });

  const bulkForm = document.querySelector("[data-bulk-form]");
  if (bulkForm) {
    const selections = [...document.querySelectorAll("[data-document-select]")];
    const counter = bulkForm.querySelector("[data-bulk-count]");
    const updateBulk = () => {
      const count = selections.filter((input) => input.checked).length;
      counter.textContent = String(count);
      bulkForm.classList.toggle("visible", count > 0);
    };
    selections.forEach((input) => input.addEventListener("change", updateBulk));
    bulkForm.querySelector("[data-clear-selection]")?.addEventListener("click", () => {
      selections.forEach((input) => { input.checked = false; });
      updateBulk();
    });
    updateBulk();
  }

  document.querySelectorAll("[data-sortable]").forEach((list) => {
    let dragged = null;
    list.querySelectorAll("[draggable=true]").forEach((item) => {
      item.addEventListener("dragstart", () => {
        dragged = item;
        item.classList.add("dragging");
      });
      item.addEventListener("dragend", () => {
        item.classList.remove("dragging");
        dragged = null;
      });
      item.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!dragged || dragged === item) return;
        const box = item.getBoundingClientRect();
        list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? item : item.nextSibling);
      });
    });
  });

  document.querySelectorAll('input[name="theme"]').forEach((input) => {
    input.addEventListener("change", () => {
      document.documentElement.dataset.theme = input.value;
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      const button = form.querySelector("button[type=submit], button:not([type])");
      if (!button || form.dataset.confirm) return;
      window.setTimeout(() => {
        button.disabled = true;
        button.classList.add("loading");
      }, 0);
    });
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("revealed");
        observer.unobserve(entry.target);
      }
    }), { threshold: 0.1 });
    document.querySelectorAll("[data-reveal]").forEach((item) => observer.observe(item));
  }
});

