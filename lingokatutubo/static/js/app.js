(function () {
  const phaseLabels = {
    uploading: "Uploading",
    queued: "Queued for processing",
    detecting: "Detecting document",
    extracting: "Extracting text and layout",
    ocr: "Running OCR",
    translating: "Translating",
    reconstructing: "Reconstructing document",
    preview_generation: "Creating preview",
    bilingual_output: "Preparing bilingual output",
    completed: "Completed",
    failed: "Failed",
  };

  const phaseProgress = {
    uploading: 3,
    queued: 5,
    detecting: 15,
    extracting: 30,
    ocr: 45,
    translating: 65,
    reconstructing: 80,
    preview_generation: 90,
    bilingual_output: 95,
    completed: 100,
  };

  function text(value) {
    return value == null ? "" : String(value);
  }

  function escapeHtml(value) {
    return text(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function csrfToken() {
    const input = document.querySelector("input[name='csrfmiddlewaretoken']");
    if (input) return input.value;
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function normalizeStatus(value) {
    return text(value).trim().toLowerCase();
  }

  function progressFor(data) {
    const phase = normalizeStatus(data.current_phase || data.status);
    const raw = data.progress_percent != null ? data.progress_percent : data.progress;
    const numeric = Number(raw);
    if (Number.isFinite(numeric)) return Math.max(0, Math.min(100, Math.round(numeric)));
    return phaseProgress[phase] || 0;
  }

  function languageName(value) {
    const names = {
      english: "English",
      filipino: "Filipino",
      cebuano: "Cebuano",
      tagabawa: "Bagobo-Tagabawa",
    };
    const key = normalizeStatus(value);
    return names[key] || text(value);
  }

  /* =========================================================
     Upload workflow
  ========================================================= */
  function initUpload() {
    const form = document.getElementById("upload-form");
    if (!form) return;

    const fileInput = document.getElementById("id_file");
    const dropZone = document.getElementById("drop-zone");
    const fileName = document.getElementById("selected-file-name");
    const fileMeta = document.getElementById("selected-file-meta");
    const readyMessage = document.getElementById("ready-message");
    const errorBox = document.getElementById("upload-error");
    const button = document.getElementById("translate-button");
    const progressCard = document.getElementById("progress-card");
    const progressTitle = document.getElementById("progress-title");
    const progressMessage = document.getElementById("progress-message");
    const progressPercent = document.getElementById("progress-percent");
    const progressFill = document.getElementById("progress-fill");
    const progressStep = document.getElementById("progress-step");
    const detectedLanguage = document.getElementById("detected-language");
    const completeActions = document.getElementById("complete-actions");
    const downloadLink = document.getElementById("download-link");
    const previewLink = document.getElementById("preview-link");
    let pollTimer = null;

    function setError(message) {
      errorBox.hidden = !message;
      errorBox.textContent = message || "";
    }

    function setProgress(data) {
      const phase = normalizeStatus(data.current_phase || data.status || "queued");
      const percent = progressFor(data);
      progressCard.hidden = false;
      if (normalizeStatus(data.status) !== "completed") {
        completeActions.hidden = true;
        downloadLink.hidden = true;
        previewLink.hidden = true;
      }
      progressTitle.textContent = phaseLabels[phase] || data.current_step || "Processing";
      progressMessage.textContent = data.phase_message || data.message || phaseLabels[phase] || "";
      progressPercent.textContent = `${percent}%`;
      progressFill.style.width = `${percent}%`;
      progressStep.textContent = data.current_step || phaseLabels[phase] || "Processing";
      progressFill.parentElement.setAttribute("aria-valuenow", String(percent));

      if (data.detected_language) {
        const confidence = data.detection_confidence != null
          ? ` (${Math.round(Number(data.detection_confidence) * 100)}% confidence)`
          : "";
        detectedLanguage.textContent = `Detected source language: ${languageName(data.detected_language)}${confidence}`;
        detectedLanguage.hidden = false;
      }
    }

    function clearPoll() {
      if (pollTimer) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
    }

    function poll(jobId, attempt) {
      if (attempt > 160) {
        setError("Translation is taking longer than expected. Refresh this page and check Recent Jobs.");
        return;
      }
      fetch(`${form.dataset.statusBase}${jobId}/`, { credentials: "same-origin" })
        .then((response) => {
          if (!response.ok) throw new Error(`Status check failed (${response.status}).`);
          return response.json();
        })
        .then((data) => {
          setProgress(data);
          const status = normalizeStatus(data.status);
          if (status === "completed") {
            clearPoll();
            button.disabled = true;
            button.textContent = "Completed";
            previewLink.hidden = !data.can_preview;
            downloadLink.hidden = !data.can_download;
            previewLink.href = `${form.dataset.previewBase}${jobId}/preview/`;
            downloadLink.href = `${form.dataset.downloadBase}${jobId}/download/`;
            completeActions.hidden = !(data.can_preview || data.can_download);
            return;
          }
          if (status === "failed") {
            clearPoll();
            button.disabled = false;
            button.textContent = "Start Translation";
            completeActions.hidden = true;
            downloadLink.hidden = true;
            previewLink.hidden = true;
            setError(data.error || data.message || "Translation failed.");
            return;
          }
          pollTimer = window.setTimeout(() => poll(jobId, attempt + 1), 1500);
        })
        .catch((error) => {
          setError(error.message || "Could not reach the server.");
          pollTimer = window.setTimeout(() => poll(jobId, attempt + 1), 2000);
        });
    }

    function updateSelectedFile() {
      const file = fileInput.files && fileInput.files[0];
      completeActions.hidden = true;
      downloadLink.hidden = true;
      previewLink.hidden = true;
      detectedLanguage.hidden = true;
      setError("");
      if (!file) {
        fileName.textContent = "Drop your document here";
        fileMeta.textContent = "PDF, DOCX, JPG, PNG, or TXT — up to 50 MB";
        readyMessage.hidden = true;
        button.disabled = true;
        return;
      }
      fileName.textContent = file.name;
      fileMeta.textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
      readyMessage.hidden = false;
      button.disabled = false;
    }

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("dragging");
      });
    });
    dropZone.addEventListener("drop", (event) => {
      if (event.dataTransfer.files.length) {
        fileInput.files = event.dataTransfer.files;
        updateSelectedFile();
      }
    });
    fileInput.addEventListener("change", updateSelectedFile);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      clearPoll();
      setError("");
      completeActions.hidden = true;
      downloadLink.hidden = true;
      previewLink.hidden = true;
      detectedLanguage.hidden = true;
      button.disabled = true;
      button.textContent = "Uploading...";
      setProgress({
        status: "uploading",
        current_phase: "uploading",
        progress_percent: 3,
        phase_message: "Uploading document.",
        current_step: "Uploading document",
      });

      fetch(form.dataset.uploadUrl, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken() },
      })
        .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
          if (!ok) {
            throw new Error(formatUploadError(data));
          }
          button.textContent = "Processing...";
          setProgress(data);
          poll(data.job_id, 0);
        })
        .catch((error) => {
          button.disabled = false;
          button.textContent = "Start Translation";
          setError(error.message || "Upload failed.");
        });
    });
  }

  function formatUploadError(data) {
    if (!data) return "Upload failed.";
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object") {
      return Object.values(data.detail)
        .flat()
        .map((entry) => entry.message || entry)
        .join(" ");
    }
    return data.error || "Upload failed.";
  }

  /* =========================================================
     Bilingual preview page
  ========================================================= */
  function initPreview() {
    const root = document.getElementById("preview-app");
    if (!root) return;
    const loading = document.getElementById("preview-loading");
    const content = document.getElementById("preview-content");
    const errorBox = document.getElementById("preview-error");
    const originalImage = document.getElementById("original-image");
    const translatedImage = document.getElementById("translated-image");
    const originalEmpty = document.getElementById("original-empty");
    const translatedEmpty = document.getElementById("translated-empty");
    const currentPageEl = document.getElementById("current-page");
    const pageCountEl = document.getElementById("page-count");
    const previousButton = document.getElementById("previous-page");
    const nextButton = document.getElementById("next-page");
    const detailsList = document.getElementById("details-list");
    const warningBox = document.getElementById("structure-warnings");
    const detailsPanel = document.querySelector(".translation-details");
    const detailsSummary = detailsPanel ? detailsPanel.querySelector("summary") : null;

    if (!content || !loading || !errorBox || !originalImage || !translatedImage) return;

    let preview = null;
    let structure = null;
    let currentPage = 1;

    if (detailsPanel && detailsSummary) {
      detailsSummary.setAttribute("aria-expanded", detailsPanel.open ? "true" : "false");
      detailsPanel.addEventListener("toggle", () => {
        detailsSummary.setAttribute("aria-expanded", detailsPanel.open ? "true" : "false");
      });
    }

    function showError(message) {
      loading.hidden = true;
      content.hidden = true;
      errorBox.hidden = false;
      errorBox.textContent = message;
    }

    function pageCount() {
      if (!preview) return 1;
      return Math.max(
        preview.original_pages ? preview.original_pages.length : 0,
        preview.translated_pages ? preview.translated_pages.length : 0,
        Number(preview.page_count || 0),
        1
      );
    }

    function setImage(img, empty, url) {
      if (url) {
        img.hidden = false;
        img.src = url;
        empty.hidden = true;
      } else {
        img.hidden = true;
        img.removeAttribute("src");
        empty.hidden = false;
      }
    }

    function renderDetails(pageIndex) {
      const page = structure && structure.pages ? structure.pages[pageIndex] : null;
      let blocks = page && page.blocks
        ? page.blocks.filter((block) => block.type === "text" || block.block_type === "text")
        : [];
      if (!blocks.length && pageIndex === 0 && preview.bilingual_first_page) {
        blocks = preview.bilingual_first_page.blocks || [];
      }

      if (!blocks.length) {
        detailsList.innerHTML = `<p class="muted">No structured text blocks for this page.</p>`;
      } else {
        detailsList.innerHTML = blocks.map((block) => {
          const source = block.source_text || block.original_text || "-";
          const translated = block.translated_text || "UNKNOWN_FOR_REVIEW";
          const needsReview = !block.translated_text || block.translated_text === "UNKNOWN_FOR_REVIEW";
          return `
            <article class="detail-row">
              <div>
                <small>Original</small>
                <p>${escapeHtml(source)}</p>
              </div>
              <div>
                <small>Translation</small>
                <p class="${needsReview ? "needs-review" : ""}">${escapeHtml(translated)}</p>
              </div>
            </article>
          `;
        }).join("");
      }

      const warnings = structure && Array.isArray(structure.warnings) ? structure.warnings : [];
      if (warnings.length) {
        warningBox.hidden = false;
        warningBox.innerHTML = `<strong>Layout warnings</strong><br>${warnings.slice(0, 6).map(escapeHtml).join("<br>")}`;
      } else {
        warningBox.hidden = true;
      }
    }

    function render() {
      const total = pageCount();
      currentPage = Math.max(1, Math.min(currentPage, total));
      const index = currentPage - 1;
      currentPageEl.textContent = String(currentPage);
      pageCountEl.textContent = String(total);
      previousButton.disabled = currentPage <= 1;
      nextButton.disabled = currentPage >= total;
      setImage(originalImage, originalEmpty, preview.original_pages && preview.original_pages[index]);
      setImage(translatedImage, translatedEmpty, preview.translated_pages && preview.translated_pages[index]);
      renderDetails(index);
    }

    previousButton.addEventListener("click", () => {
      currentPage -= 1;
      render();
    });
    nextButton.addEventListener("click", () => {
      currentPage += 1;
      render();
    });

    Promise.all([
      fetch(root.dataset.previewUrl, { credentials: "same-origin" }),
      fetch(root.dataset.structureUrl, { credentials: "same-origin" }),
    ])
      .then(async ([previewResponse, structureResponse]) => {
        if (!previewResponse.ok) {
          const data = await previewResponse.json().catch(() => ({}));
          throw new Error(data.error || `Preview failed (${previewResponse.status}).`);
        }
        preview = await previewResponse.json();
        structure = structureResponse.ok ? await structureResponse.json() : null;
        loading.hidden = true;
        content.hidden = false;
        render();
      })
      .catch((error) => showError(error.message || "Could not load preview."));
  }

  /* =========================================================
     Mobile navigation
  ========================================================= */
  function initMobileNav() {
    const hamburger = document.getElementById("nav-hamburger");
    const navLinks = document.getElementById("nav-links");
    if (!hamburger || !navLinks) return;

    hamburger.addEventListener("click", () => {
      const isOpen = navLinks.classList.toggle("open");
      hamburger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });

    navLinks.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        navLinks.classList.remove("open");
        hamburger.setAttribute("aria-expanded", "false");
      });
    });

    document.addEventListener("click", (event) => {
      if (!hamburger.contains(event.target) && !navLinks.contains(event.target)) {
        navLinks.classList.remove("open");
        hamburger.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* =========================================================
     Dismissible flash messages
  ========================================================= */
  function initMessageDismiss() {
    document.querySelectorAll(".message-dismiss").forEach((btn) => {
      btn.addEventListener("click", () => {
        const msg = btn.closest(".message");
        if (msg) {
          msg.style.transition = "opacity 200ms ease, transform 200ms ease";
          msg.style.opacity = "0";
          msg.style.transform = "translateY(-4px)";
          setTimeout(() => msg.remove(), 210);
        }
      });
    });
  }

  /* =========================================================
     Boot
  ========================================================= */
  document.addEventListener("DOMContentLoaded", () => {
    initUpload();
    initPreview();
    initMobileNav();
    initMessageDismiss();
  });
})();
