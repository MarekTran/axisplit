// Small SSR enhancements. Every destructive action still needs an explicit form submit.
document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    const input = document.getElementById(copy.dataset.copy);
    try {
      await navigator.clipboard.writeText(input.value);
      copy.textContent = "Copied ✓";
      setTimeout(() => { copy.textContent = "Copy link"; }, 2500);
    } catch {
      input.focus();
      input.select();
      copy.textContent = "Select & copy";
    }
  }
  const opener = event.target.closest("[data-dialog]");
  if (opener) document.getElementById(opener.dataset.dialog).showModal();
  const closer = event.target.closest("[data-close-dialog]");
  if (closer) closer.closest("dialog").close();
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches("[data-busy]")) return;
  if (form.dataset.submitting) { event.preventDefault(); return; }
  form.dataset.submitting = "true";
  form.setAttribute("aria-busy", "true");
  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = form.dataset.busy;
  form.querySelector(".busy-help")?.removeAttribute("hidden");
});

// Back/forward cache can restore a submitted form with its button still disabled.
window.addEventListener("pageshow", (event) => {
  if (event.persisted) location.reload();
});
