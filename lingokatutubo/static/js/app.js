(function () {
  const activeStatuses = new Set(["queued", "processing", "retrying"]);
  const terminalStatuses = new Set(["completed", "failed"]);

  const phaseLabels = {
    uploading: "Uploading",
    queued: "Queued for processing",
    retrying: "Retrying",
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
    retrying: 5,
    detecting: 15,
    extracting: 30,
    ocr: 45,
    translating: 65,
    reconstructing: 80,
    preview_generation: 90,
    bilingual_output: 95,
    completed: 100,
  };

  const controllers = new Map();

  function text(value) {
    return value == null ? "" : String(value);
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

  function statusLabel(value) {
    const status = normalizeStatus(value);
    if (status === "queued") return "Pending";
    return phaseLabels[status] || (status ? status.charAt(0).toUpperCase() + status.slice(1) : "Unknown");
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

  function formatDateTime(value) {
    if (!value) return "Not available";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return text(value);
    return date.toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function nowLabel() {
    return new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function setHidden(element, hidden) {
    if (element) element.hidden = hidden;
  }

  function setText(element, value) {
    if (element) element.textContent = value;
  }

  function setError(errorBox, message) {
    if (!errorBox) return;
    errorBox.hidden = !message;
    errorBox.textContent = message || "";
  }

  // Django can hand back an HTML page instead of JSON (login redirect, CSRF
  // failure, 404/500 error page) whenever something between the browser and
  // the view doesn't go as planned. Parsing that as JSON throws a raw
  // "Unexpected token '<'" SyntaxError — never call response.json() without
  // checking Content-Type first, or that raw parser error reaches the user.
  function parseJsonResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return response.text().then((bodyText) => {
        console.error(
          "Expected JSON but received:",
          response.status,
          contentType,
          bodyText.slice(0, 300)
        );
        throw new Error("The server returned an unexpected response. Please refresh and try again.");
      });
    }
    return response.json().then((data) => ({ response, data }));
  }

  class PollingController {
    constructor(key, statusUrl, handlers) {
      this.key = key;
      this.statusUrl = statusUrl;
      this.handlers = handlers || {};
      this.timer = null;
      this.inFlight = false;
      this.stopped = false;
      this.failureCount = 0;
      this.lastData = null;
      this.boundVisibilityHandler = () => this.handleVisibilityChange();
      document.addEventListener("visibilitychange", this.boundVisibilityHandler);
    }

    static start(key, statusUrl, handlers) {
      if (controllers.has(key)) {
        return controllers.get(key);
      }
      const controller = new PollingController(key, statusUrl, handlers);
      controllers.set(key, controller);
      controller.poll(0);
      return controller;
    }

    stop() {
      this.stopped = true;
      if (this.timer) {
        window.clearTimeout(this.timer);
        this.timer = null;
      }
      document.removeEventListener("visibilitychange", this.boundVisibilityHandler);
      controllers.delete(this.key);
    }

    handleVisibilityChange() {
      if (this.stopped) return;
      if (!document.hidden && this.lastData && activeStatuses.has(normalizeStatus(this.lastData.status))) {
        this.schedule(0);
      }
    }

    schedule(delay) {
      if (this.stopped) return;
      if (this.timer) window.clearTimeout(this.timer);
      this.timer = window.setTimeout(() => this.poll(), delay);
    }

    nextDelay() {
      const visibleDelay = this.failureCount ? 3000 : 1500;
      const hiddenDelay = this.failureCount ? 15000 : 10000;
      return document.hidden ? hiddenDelay : visibleDelay;
    }

    poll(delayOverride) {
      if (this.stopped || this.inFlight) return;
      if (typeof delayOverride === "number" && delayOverride > 0) {
        this.schedule(delayOverride);
        return;
      }
      this.inFlight = true;
      fetch(this.statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(parseJsonResponse)
        .then(({ response, data }) => {
          if (!response.ok) throw new Error(data.error || `Status check failed (${response.status}).`);
          this.failureCount = 0;
          this.lastData = data;
          if (this.handlers.onUpdate) this.handlers.onUpdate(data);
          if (this.handlers.onNetworkState) this.handlers.onNetworkState("", data);
          const status = normalizeStatus(data.status);
          if (terminalStatuses.has(status) || !activeStatuses.has(status)) {
            if (this.handlers.onTerminal) this.handlers.onTerminal(data);
            this.stop();
            return;
          }
          this.schedule(this.nextDelay());
        })
        .catch((error) => {
          this.failureCount += 1;
          if (this.handlers.onNetworkState) {
            this.handlers.onNetworkState(
              `${error.message || "Could not reach the server."} Retrying status check...`,
              this.lastData
            );
          }
          this.schedule(this.nextDelay());
        })
        .finally(() => {
          this.inFlight = false;
        });
    }
  }

  window.LingoKatutuboPolling = {
    activeStatuses,
    controllers,
    start: PollingController.start,
  };

  function updateProgressDom(elements, data) {
    const phase = normalizeStatus(data.current_phase || data.status || "queued");
    const status = normalizeStatus(data.status);
    const percent = progressFor(data);
    const message = data.phase_message || data.message || phaseLabels[phase] || "";
    setHidden(elements.progressCard, false);
    setText(elements.title, phaseLabels[phase] || data.current_step || statusLabel(status));
    setText(elements.message, message);
    setText(elements.percent, `${percent}%`);
    if (elements.fill) elements.fill.style.width = `${percent}%`;
    if (elements.progressbar) elements.progressbar.setAttribute("aria-valuenow", String(percent));
    setText(elements.step, data.current_step || phaseLabels[phase] || statusLabel(status));
  }

  function updateLanguageDom(element, data) {
    if (!element || !data.detected_language) return;
    const confidence = data.detection_confidence != null
      ? ` (${Math.round(Number(data.detection_confidence) * 100)}% confidence)`
      : "";
    element.textContent = `Detected source language: ${languageName(data.detected_language)}${confidence}`;
    element.hidden = false;
  }

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
    const progressFill = document.getElementById("progress-fill");
    const detectedLanguage = document.getElementById("detected-language");
    const completeActions = document.getElementById("complete-actions");
    const downloadLink = document.getElementById("download-link");
    const previewLink = document.getElementById("preview-link");
    const backgroundMessage = document.getElementById("background-processing-message");
    const statusLink = document.getElementById("status-link");

    const progressElements = {
      progressCard,
      title: document.getElementById("progress-title"),
      message: document.getElementById("progress-message"),
      percent: document.getElementById("progress-percent"),
      fill: progressFill,
      progressbar: progressFill ? progressFill.parentElement : null,
      step: document.getElementById("progress-step"),
    };

    let isUploading = false;
    let isSubmitting = false;
    let currentJobId = null;

    function beforeUnloadHandler(event) {
      if (!isUploading || currentJobId) return undefined;
      const message = "Your document is still uploading. Leaving now may cancel the upload.";
      event.preventDefault();
      event.returnValue = message;
      return message;
    }

    function enableUploadLeaveWarning() {
      window.addEventListener("beforeunload", beforeUnloadHandler);
    }

    function disableUploadLeaveWarning() {
      window.removeEventListener("beforeunload", beforeUnloadHandler);
    }

    function resetOutputLinks() {
      completeActions.hidden = true;
      downloadLink.hidden = true;
      previewLink.hidden = true;
    }

    function showBackgroundMessage(jobId) {
      if (!backgroundMessage) return;
      backgroundMessage.hidden = false;
      if (statusLink) statusLink.href = `${form.dataset.previewBase}${jobId}/`;
    }

    function handleTerminal(data) {
      const status = normalizeStatus(data.status);
      if (status === "completed") {
        button.disabled = true;
        button.textContent = "Completed";
        previewLink.hidden = !data.can_preview;
        downloadLink.hidden = !data.can_download;
        previewLink.href = `${form.dataset.previewBase}${data.job_id}/preview/`;
        downloadLink.href = `${form.dataset.downloadBase}${data.job_id}/download/`;
        completeActions.hidden = !(data.can_preview || data.can_download);
        return;
      }
      if (status === "failed") {
        button.disabled = false;
        button.textContent = "Start Translation";
        resetOutputLinks();
        setError(errorBox, data.error || data.message || "Translation failed.");
      }
    }

    function updateSelectedFile() {
      const file = fileInput.files && fileInput.files[0];
      resetOutputLinks();
      setHidden(backgroundMessage, true);
      setHidden(detectedLanguage, true);
      setError(errorBox, "");
      if (!file) {
        fileName.textContent = "Drop your document here";
        fileMeta.textContent = "PDF, DOCX, JPG, PNG, or TXT - up to 50 MB";
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
      if (!file || isSubmitting) return;
      isSubmitting = true;
      isUploading = true;
      currentJobId = null;
      enableUploadLeaveWarning();
      setError(errorBox, "");
      resetOutputLinks();
      setHidden(backgroundMessage, true);
      setHidden(detectedLanguage, true);
      button.disabled = true;
      button.textContent = "Uploading...";
      updateProgressDom(progressElements, {
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
        headers: {
          "X-CSRFToken": csrfToken(),
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      })
        .then(parseJsonResponse)
        .then(({ response, data }) => {
          if (!response.ok) throw new Error(formatUploadError(data));
          currentJobId = data.job_id;
          isUploading = false;
          disableUploadLeaveWarning();
          button.textContent = "Processing...";
          showBackgroundMessage(data.job_id);
          updateProgressDom(progressElements, data);
          updateLanguageDom(detectedLanguage, data);
          PollingController.start(`upload:${data.job_id}`, `${form.dataset.statusBase}${data.job_id}/`, {
            onUpdate: (payload) => {
              updateProgressDom(progressElements, payload);
              updateLanguageDom(detectedLanguage, payload);
            },
            onTerminal: handleTerminal,
            onNetworkState: (message) => setError(errorBox, message),
          });
        })
        .catch((error) => {
          isUploading = false;
          disableUploadLeaveWarning();
          isSubmitting = false;
          button.disabled = false;
          button.textContent = "Start Translation";
          setHidden(progressCard, true);
          setError(errorBox, error.message || "Upload failed.");
        });
    });
  }

  function initStatusPage() {
    const root = document.querySelector("[data-status-poller]");
    if (!root) return;

    const progressFill = document.getElementById("status-monitor-fill");
    const progressElements = {
      progressCard: root,
      title: document.getElementById("status-monitor-title"),
      message: document.getElementById("status-monitor-message"),
      percent: document.getElementById("status-monitor-percent"),
      fill: progressFill,
      progressbar: progressFill ? progressFill.parentElement : null,
      step: document.getElementById("job-current-step"),
    };
    const network = document.getElementById("status-monitor-network");
    const lastCheck = document.getElementById("status-monitor-last-check");
    const phase = document.getElementById("status-monitor-phase");
    const updated = document.getElementById("status-monitor-updated");
    const phaseValue = document.getElementById("job-current-phase");
    const messageValue = document.getElementById("job-phase-message");
    const progressText = document.getElementById("job-progress-text");
    const retryingState = document.getElementById("job-retrying-state");
    const longMessage = document.getElementById("status-monitor-long");
    const failedGuidance = document.getElementById("failed-guidance");
    const statusPill = document.getElementById("job-status-pill");
    const previewAction = document.getElementById("job-preview-action");
    const downloadAction = document.getElementById("job-download-action");
    const reloadButton = document.getElementById("reload-status-button");

    function applyStatus(data) {
      const status = normalizeStatus(data.status);
      const percent = progressFor(data);
      updateProgressDom(progressElements, data);
      setText(phase, data.current_phase || status);
      setText(updated, formatDateTime(data.updated_at));
      setText(lastCheck, nowLabel());
      setText(phaseValue, data.current_phase || status);
      setText(messageValue, data.phase_message || data.message || "");
      setText(progressText, `${percent}%`);
      setText(retryingState, status === "retrying" ? "Yes" : "No");
      setHidden(longMessage, !data.is_taking_longer);
      if (statusPill) {
        statusPill.textContent = statusLabel(status);
        statusPill.className = `status-pill ${status === "queued" ? "pending" : status}`;
      }
      setHidden(previewAction, !data.can_preview);
      setHidden(downloadAction, !data.can_download);
      if (previewAction && data.can_preview && !previewAction.textContent.trim()) {
        previewAction.textContent = "Preview Bilingual";
      }
      if (downloadAction && data.can_download && !downloadAction.textContent.trim()) {
        downloadAction.textContent = "Download";
      }
      if (status === "failed") {
        failedGuidance.hidden = false;
        failedGuidance.textContent = `${data.error || data.message || "Translation failed."} Check the document quality or try uploading again. If this repeats, contact an administrator with this job ID.`;
      }
    }

    function networkState(message) {
      if (!network) return;
      network.hidden = !message;
      network.textContent = message || "";
    }

    let controller = null;
    const handlers = {
      onUpdate: applyStatus,
      onTerminal: (data) => {
        applyStatus(data);
        if (normalizeStatus(data.status) === "completed") {
          showStatusMessage("Translated file is ready.", "success");
        }
        controller = null;
      },
      onNetworkState: networkState,
    };
    const initialStatus = normalizeStatus(root.dataset.initialStatus);
    if (activeStatuses.has(initialStatus)) {
      controller = PollingController.start(`detail:${root.dataset.jobId}`, root.dataset.statusUrl, handlers);
    }

    if (reloadButton) {
      reloadButton.addEventListener("click", () => {
        networkState("");
        if (controller) {
          controller.poll();
          return;
        }
        fetch(root.dataset.statusUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then(parseJsonResponse)
          .then(({ response, data }) => {
            if (!response.ok) throw new Error(data.error || `Status check failed (${response.status}).`);
            applyStatus(data);
          })
          .catch((error) => networkState(`${error.message || "Could not reach the server."} Retrying status check...`));
      });
    }
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

  function initRenderedPreviewControls() {
    const root = document.getElementById("preview-app");
    const scope = document.querySelector("[data-preview-scope]");
    const buttons = document.querySelectorAll("[data-preview-zoom-action]");
    if (!root || !scope || !buttons.length) return;

    const MIN_ZOOM = 0.75;
    const MAX_ZOOM = 1.5;
    const ZOOM_STEP = 0.15;
    let zoom = 1;

    function updateButtons(activeAction) {
      buttons.forEach((button) => {
        const isActive = button.dataset.previewZoomAction === activeAction;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
    }

    function setZoom(nextZoom, activeAction) {
      zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
      const fitWidth = activeAction === "fit" || activeAction === "reset";
      scope.style.setProperty("--preview-zoom", zoom.toFixed(2));
      scope.style.setProperty("--preview-card-width", `${Math.round(zoom * 100)}%`);
      scope.classList.toggle("is-fit-width", fitWidth);
      updateButtons(activeAction);
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.previewZoomAction;
        if (action === "fit" || action === "reset") {
          setZoom(1, action);
        } else if (action === "in") {
          setZoom(zoom + ZOOM_STEP, "in");
        } else if (action === "out") {
          setZoom(zoom - ZOOM_STEP, "out");
        }
      });
    });

    setZoom(1, "fit");
  }

  function initPreviewLinks() {
    const links = document.querySelectorAll(".js-preview-link");
    if (!links.length) return;

    const overlay = document.getElementById("page-transition-overlay");
    const overlayText = document.getElementById("page-transition-text");
    const errorBanner = document.getElementById("js-error-banner");
    const errorBannerText = document.getElementById("js-error-banner-text");
    // Minimum time the loading state stays visible before navigating, so the
    // transition reads as intentional instead of a sudden visual jump.
    const MIN_LOADING_MS = 2500;
    const FAILSAFE_TIMEOUT_MS = 12000;

    function showOverlay(message) {
      if (!overlay) return;
      if (overlayText && message) overlayText.textContent = message;
      overlay.classList.add("is-visible");
      overlay.setAttribute("aria-hidden", "false");
    }

    function hideOverlay() {
      if (!overlay) return;
      overlay.classList.remove("is-visible");
      overlay.setAttribute("aria-hidden", "true");
    }

    function showOpenFailure() {
      if (!errorBanner || !errorBannerText) return;
      errorBannerText.textContent = "Could not open the bilingual preview. Please try again.";
      errorBanner.hidden = false;
    }

    links.forEach((link) => {
      link.addEventListener("click", (event) => {
        // Never intercept a hidden link, a link already loading, or a
        // modified/non-primary click (new tab, new window, etc.) — those
        // must keep their normal browser behavior.
        if (link.hidden || link.classList.contains("is-loading")) {
          event.preventDefault();
          return;
        }
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return;
        }
        const href = link.getAttribute("href");
        if (!href || href === "#") return;

        event.preventDefault();
        const loadingText = link.dataset.loadingText || "Preparing bilingual preview…";
        link.dataset.originalText = link.textContent;
        link.classList.add("is-loading");
        link.setAttribute("aria-disabled", "true");
        link.textContent = loadingText;
        showOverlay(loadingText);

        const failsafeTimer = window.setTimeout(() => {
          link.classList.remove("is-loading");
          link.removeAttribute("aria-disabled");
          link.textContent = link.dataset.originalText || loadingText;
          hideOverlay();
          showOpenFailure();
        }, FAILSAFE_TIMEOUT_MS);

        // Hold the loading state for a fixed minimum duration, then navigate.
        // Reduced-motion users still get this same wait — only the spinner
        // animation is suppressed (see the prefers-reduced-motion CSS rule).
        window.setTimeout(() => {
          window.clearTimeout(failsafeTimer);
          window.location.href = href;
        }, MIN_LOADING_MS);
      });
    });
  }

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

  function dismissMessage(msg) {
    msg.style.transition = "opacity 200ms ease, transform 200ms ease";
    msg.style.opacity = "0";
    msg.style.transform = "translateY(-4px)";
    setTimeout(() => msg.remove(), 210);
  }

  function initMessageDismiss() {
    document.querySelectorAll(".message-dismiss").forEach((btn) => {
      btn.addEventListener("click", () => {
        const msg = btn.closest(".message");
        if (msg) dismissMessage(msg);
      });
    });
  }

  // Shows a dismissible, accessible status message (e.g. "Translated file is
  // ready.") detected live via polling, without a page reload. Reuses the
  // same markup/classes as Django's server-rendered messages so it looks and
  // behaves identically, and creates the messages region if this page never
  // rendered one (no messages were queued at request time).
  function showStatusMessage(messageText, tone) {
    let region = document.getElementById("messages-region");
    if (!region) {
      region = document.createElement("div");
      region.className = "messages";
      region.id = "messages-region";
      region.setAttribute("aria-label", "Notifications");
      const header = document.querySelector(".site-header");
      if (header) {
        header.insertAdjacentElement("afterend", region);
      } else {
        document.body.prepend(region);
      }
    }

    const item = document.createElement("div");
    item.className = `message ${tone || "info"}`;
    item.setAttribute("role", "status");
    item.setAttribute("aria-live", "polite");

    const span = document.createElement("span");
    span.textContent = messageText;

    const dismissButton = document.createElement("button");
    dismissButton.type = "button";
    dismissButton.className = "message-dismiss";
    dismissButton.setAttribute("aria-label", "Dismiss notification");
    dismissButton.textContent = "✕";
    dismissButton.addEventListener("click", () => dismissMessage(item));

    item.append(span, dismissButton);
    region.appendChild(item);
  }

  document.addEventListener("DOMContentLoaded", () => {
    initUpload();
    initStatusPage();
    initRenderedPreviewControls();
    initPreviewLinks();
    initMobileNav();
    initMessageDismiss();
  });
})();
