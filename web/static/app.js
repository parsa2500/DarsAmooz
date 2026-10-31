(() => {
  "use strict";

  const LETTERS = ["الف", "ب", "ج", "د"];
  const DIFF_FA = { easy: "آسان", medium: "متوسط", hard: "سخت" };

  const els = {
    bookTitle: document.getElementById("bookTitle"),
    providerLine: document.getElementById("providerLine"),
    versionLine: document.getElementById("versionLine"),
    form: document.getElementById("generateForm"),
    chapter: document.getElementById("chapter"),
    count: document.getElementById("count"),
    sample: document.getElementById("sample"),
    btnGenerate: document.getElementById("btnGenerate"),
    btnCancel: document.getElementById("btnCancel"),
    statusStrip: document.getElementById("statusStrip"),
    statusText: document.getElementById("statusText"),
    logBox: document.getElementById("logBox"),
    previewEmpty: document.getElementById("previewEmpty"),
    questionList: document.getElementById("questionList"),
    pathsLine: document.getElementById("pathsLine"),
    exportActions: document.getElementById("exportActions"),
    btnCopyJson: document.getElementById("btnCopyJson"),
    btnCopyDir: document.getElementById("btnCopyDir"),
  };

  let pollTimer = null;
  let lastPaths = null;
  let exportsDir = "";

  function qs() {
    return new URLSearchParams(window.location.search);
  }

  function setStatus(state, text) {
    els.statusStrip.dataset.state = state;
    els.statusText.textContent = text;
  }

  function setBusy(busy) {
    els.btnGenerate.disabled = busy;
    els.btnCancel.disabled = !busy;
    els.chapter.disabled = busy;
    els.count.disabled = busy;
    els.sample.disabled = busy;
  }

  function showLog(lines) {
    if (!lines || !lines.length) {
      els.logBox.hidden = true;
      els.logBox.textContent = "";
      return;
    }
    els.logBox.hidden = false;
    els.logBox.textContent = lines.join("\n");
    els.logBox.scrollTop = els.logBox.scrollHeight;
  }

  function formatDetail(detail) {
    if (detail == null) return null;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (typeof d === "string" ? d : d.msg || JSON.stringify(d)))
        .filter(Boolean)
        .join("؛ ");
    }
    if (typeof detail === "object" && detail.msg) return detail.msg;
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }

  async function api(path, options) {
    let res;
    try {
      res = await fetch(path, {
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        ...options,
      });
    } catch {
      throw new Error(
        "ارتباط با سرور قطع است. لانچر را دوباره اجرا کنید (scripts\\launch.bat)."
      );
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      const detail = data && (data.detail || data.error);
      const msg = formatDetail(detail) || res.statusText;
      throw new Error(msg || "خطای ناشناخته");
    }
    return data;
  }

  async function loadMeta() {
    const meta = await api("/api/meta");
    els.bookTitle.textContent = meta.book_title;
    exportsDir = meta.exports_dir || "";
    const keyNote = meta.has_api_key
      ? `کلید API آماده`
      : `کلید API نیست — از حالت نمونه استفاده کنید`;
    const modelBit = meta.model ? ` · ${meta.model}` : "";
    els.providerLine.textContent = `${meta.provider}${modelBit} · ${keyNote}`;
    els.versionLine.textContent = `نسخه ${meta.version}`;

    els.chapter.replaceChildren();
    for (const ch of meta.chapters) {
      const opt = document.createElement("option");
      opt.value = String(ch.number);
      opt.textContent = `فصل ${ch.number} — ${ch.title}`;
      els.chapter.appendChild(opt);
    }

    const params = qs();
    if (params.has("chapter")) els.chapter.value = params.get("chapter");
    if (params.has("count")) els.count.value = params.get("count");
    if (params.get("sample") === "1" || params.get("sample") === "true") {
      els.sample.checked = true;
    }
  }

  function renderQuestions(payload) {
    const questions = payload.questions || [];
    lastPaths = payload.paths || null;

    if (!questions.length) {
      els.previewEmpty.hidden = false;
      els.questionList.hidden = true;
      els.pathsLine.hidden = true;
      els.exportActions.hidden = true;
      return;
    }

    els.previewEmpty.hidden = true;
    els.questionList.hidden = false;
    els.exportActions.hidden = false;
    els.questionList.replaceChildren();

    if (lastPaths) {
      els.pathsLine.hidden = false;
      els.pathsLine.textContent = [
        lastPaths.json && `JSON: ${lastPaths.json}`,
        lastPaths.csv && `CSV: ${lastPaths.csv}`,
        lastPaths.md && `MD: ${lastPaths.md}`,
      ]
        .filter(Boolean)
        .join("  |  ");
    }

    for (const q of questions) {
      const li = document.createElement("li");
      const diff = DIFF_FA[q.difficulty] || q.difficulty || "";
      const meta = document.createElement("div");
      meta.className = "q-meta";
      meta.innerHTML = `<span class="q-num"></span>`;
      if (diff) {
        const b = document.createElement("span");
        b.className = "badge";
        b.textContent = diff;
        meta.appendChild(b);
      }
      if (q.is_sample) {
        const b = document.createElement("span");
        b.className = "badge badge-sample";
        b.textContent = "نمونه";
        meta.appendChild(b);
      }
      if (q.id) {
        const b = document.createElement("span");
        b.className = "badge";
        b.textContent = q.id;
        meta.appendChild(b);
      }

      const stem = document.createElement("p");
      stem.className = "q-stem";
      stem.textContent = q.stem;

      const opts = document.createElement("ul");
      opts.className = "options";
      (q.options || []).forEach((text, i) => {
        const o = document.createElement("li");
        if (i === q.correct_index) o.classList.add("is-correct");
        o.innerHTML = `<span class="opt-letter">${LETTERS[i] || i + 1}</span>`;
        const span = document.createElement("span");
        span.textContent = text;
        o.appendChild(span);
        opts.appendChild(o);
      });

      const details = document.createElement("details");
      details.className = "answer-details";
      const summary = document.createElement("summary");
      summary.textContent = `پاسخ و توضیح (${LETTERS[q.correct_index] || "?"})`;
      const expl = document.createElement("p");
      expl.className = "explanation";
      expl.textContent = q.explanation || "";
      details.appendChild(summary);
      details.appendChild(expl);

      li.appendChild(meta);
      li.appendChild(stem);
      li.appendChild(opts);
      li.appendChild(details);
      els.questionList.appendChild(li);
    }
  }

  async function loadExport(prefix) {
    const data = await api(`/api/exports/${encodeURIComponent(prefix)}`);
    renderQuestions(data);
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function onJobUpdate(job) {
    showLog(job.logs);
    const map = {
      queued: "در صف…",
      running: "در حال تولید…",
      succeeded: "تولید کامل شد",
      failed: job.error || "خطا در تولید",
      cancelled: "لغو شد",
    };
    setStatus(job.status, map[job.status] || job.status);

    if (job.status === "succeeded" && job.prefix) {
      setBusy(false);
      stopPoll();
      try {
        await loadExport(job.prefix);
      } catch (err) {
        setStatus("failed", err.message);
      }
    } else if (job.status === "failed" || job.status === "cancelled") {
      setBusy(false);
      stopPoll();
    }
  }

  function startPoll(jobId) {
    stopPoll();
    pollTimer = setInterval(async () => {
      try {
        const job = await api(`/api/jobs/${jobId}`);
        await onJobUpdate(job);
      } catch (err) {
        stopPoll();
        setBusy(false);
        setStatus("failed", err.message);
      }
    }, 900);
  }

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      chapter: Number(els.chapter.value),
      count: Number(els.count.value),
      sample: els.sample.checked,
    };
    setBusy(true);
    setStatus("running", "شروع جاب…");
    showLog([]);
    try {
      const job = await api("/api/generate", {
        method: "POST",
        body: JSON.stringify(body),
      });
      await onJobUpdate(job);
      if (job.status === "queued" || job.status === "running") {
        startPoll(job.id);
      } else {
        setBusy(false);
      }
    } catch (err) {
      setBusy(false);
      setStatus("failed", err.message);
    }
  });

  els.btnCancel.addEventListener("click", async () => {
    try {
      const res = await api("/api/cancel", { method: "POST", body: "{}" });
      if (res.job) await onJobUpdate(res.job);
      else setStatus("cancelled", "لغو شد");
    } catch (err) {
      setStatus("failed", err.message);
    }
  });

  async function copyText(text) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setStatus(els.statusStrip.dataset.state || "idle", "کپی شد");
    } catch {
      setStatus("failed", "کپی در کلیپ‌بورد ممکن نشد");
    }
  }

  els.btnCopyJson.addEventListener("click", () => {
    copyText(lastPaths && lastPaths.json);
  });
  els.btnCopyDir.addEventListener("click", () => {
    copyText(exportsDir);
  });

  loadMeta().catch((err) => {
    setStatus("failed", err.message || "بارگذاری متا ناموفق بود");
  });
})();
