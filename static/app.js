const coverInput = document.querySelector("#coverInput");
const coverPreview = document.querySelector("#coverPreview");
const stegoPreview = document.querySelector("#stegoPreview");
const hideForm = document.querySelector("#hideForm");
const hideStatus = document.querySelector("#hideStatus");
const extractForm = document.querySelector("#extractForm");
const extractStatus = document.querySelector("#extractStatus");
const secretMode = document.querySelector("#secretMode");
const textSecretWrap = document.querySelector("#textSecretWrap");
const fileSecretWrap = document.querySelector("#fileSecretWrap");
const capacityLabel = document.querySelector("#capacityLabel");

const outputEls = {
  coverSize: document.querySelector("#coverSize"),
  stegoSize: document.querySelector("#stegoSize"),
  changedPixels: document.querySelector("#changedPixels"),
  psnr: document.querySelector("#psnr"),
  analysisCopy: document.querySelector("#analysisCopy"),
  histogramImage: document.querySelector("#histogramImage"),
  histogramNote: document.querySelector("#histogramNote"),
  downloadStego: document.querySelector("#downloadStego"),
  downloadExtracted: document.querySelector("#downloadExtracted"),
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function setStatus(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function enableLink(element, href) {
  element.href = href;
  element.classList.remove("disabled");
}

coverInput.addEventListener("change", () => {
  const file = coverInput.files[0];
  if (!file) return;
  coverPreview.src = URL.createObjectURL(file);
  capacityLabel.textContent = `Selected: ${file.name}`;
});

document.querySelectorAll(".segmented button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segmented button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    secretMode.value = button.dataset.mode;
    textSecretWrap.classList.toggle("hidden", button.dataset.mode !== "text");
    fileSecretWrap.classList.toggle("hidden", button.dataset.mode !== "file");
  });
});

hideForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(hideStatus, "Generating stego image...");
  const formData = new FormData(hideForm);

  try {
    const response = await fetch("/api/hide", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not generate stego image.");

    const stats = data.stats;
    stegoPreview.src = `${data.stego_url}?t=${Date.now()}`;
    outputEls.histogramImage.src = `${data.histogram_url}?t=${Date.now()}`;
    outputEls.coverSize.textContent = formatBytes(stats.cover_size_bytes);
    outputEls.stegoSize.textContent = formatBytes(stats.stego_size_bytes);
    outputEls.changedPixels.textContent = `${stats.changed_pixels} / ${stats.total_pixels}`;
    outputEls.psnr.textContent = Number.isFinite(stats.psnr_db) ? `${stats.psnr_db.toFixed(2)} dB` : "Infinite";

    const difference = stats.stego_size_bytes - stats.cover_size_bytes;
    outputEls.analysisCopy.textContent =
      `The images should look almost identical because only the last color bit is changed. ` +
      `The stego image is ${formatBytes(Math.abs(difference))} ` +
      `${difference >= 0 ? "larger" : "smaller"} because PNG compression depends on pixel patterns.`;
    outputEls.histogramNote.innerHTML =
      `<strong>Histogram explanation</strong>` +
      `<p>The solid lines are the cover image and the dotted lines are the stego image. ` +
      `In this result, only ${stats.changed_pixels} of ${stats.total_pixels} pixels changed, so the histogram lines remain very close together. ` +
      `Small differences may appear because the hidden data slightly moves some red, green, or blue values up or down by 1. ` +
      `A PSNR of ${Number.isFinite(stats.psnr_db) ? stats.psnr_db.toFixed(2) + " dB" : "infinite"} means the visual quality is very high.</p>`;

    enableLink(outputEls.downloadStego, data.stego_url);
    setStatus(hideStatus, "");
  } catch (error) {
    setStatus(hideStatus, error.message, true);
  }
});

extractForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(extractStatus, "Extracting hidden file...");
  const formData = new FormData(extractForm);

  try {
    const response = await fetch("/api/extract", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not extract file.");

    outputEls.downloadExtracted.textContent = `Download ${data.file_name}`;
    outputEls.downloadExtracted.download = data.file_name;
    enableLink(outputEls.downloadExtracted, data.download_url);
    setStatus(extractStatus, `Extracted ${data.file_name} (${formatBytes(data.file_size_bytes)}).`);
  } catch (error) {
    setStatus(extractStatus, error.message, true);
  }
});
