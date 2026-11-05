// After a photo is chosen, the uploader drags over the item lines and prices. OCR then reads only
// that area, so the total and other numbers on the receipt can't be mistaken for items.
(() => {
  const input = document.getElementById("receipt-image");
  const dialog = document.getElementById("focus-dialog");
  if (!input || !dialog) return;
  const form = input.form;
  const image = document.getElementById("focus-preview");
  const area = dialog.querySelector(".focus-rect");
  const detect = dialog.querySelector('[data-focus="detect"]');
  let focus = null;

  dragRect(image.parentElement, image, () => dialog.open, (rect, done) => {
    area.hidden = false;
    placeRect(area, rect);
    if (!done) return;
    focus = rect.w >= 0.05 && rect.h >= 0.05 ? rect : null;  // A tap is not an area.
    area.hidden = !focus;
    detect.disabled = !focus;
  });

  input.addEventListener("change", () => {
    if (!input.files.length) return;
    URL.revokeObjectURL(image.src);
    image.src = URL.createObjectURL(input.files[0]);
    focus = null;
    area.hidden = true;
    detect.disabled = true;
    dialog.returnValue = "";
    dialog.showModal();
  });

  function upload(withFocus) {
    for (const key of ["x", "y", "w", "h"]) {
      const field = form.elements[`focus_${key}`];
      field.disabled = !withFocus;  // Disabled fields aren't sent: no focus means the whole photo.
      if (withFocus) field.value = focus[key].toFixed(4);
    }
    dialog.close("upload");
    form.requestSubmit();
  }
  detect.addEventListener("click", () => upload(true));
  dialog.querySelector('[data-focus="whole"]').addEventListener("click", () => upload(false));
  // Cancelled: forget the file so picking the same photo again still opens this step.
  dialog.addEventListener("close", () => { if (dialog.returnValue !== "upload") input.value = ""; });
})();
