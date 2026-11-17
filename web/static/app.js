(() => {
  const $ = (id) => document.getElementById(id);

  const form = $("generate-form");
  const chapterEl = $("chapter");
  const countEl = $("count");
  const sampleEl = $("sample");
  const verifyEl = $("verify");
  const balanceEl = $("balance");
  const styleMineEl = $("style-mine");
  const styleMineHint = $("style-mine-hint");
  const mixEl = $("difficulty-mix");
  const patternMixEl = $("pattern-mix");
  const PATTERN_FA = {
    tarifi: "تعریفی",
    mafhumi: "مفهومی",
    halli: "حلی",
    tarkibi: "ترکیبی",
  };
  const profileEl = $("export-profile");
  const providerEl = $("llm-provider");
  const modelEl = $("llm-model");
  const modelListEl = $("llm-model-suggestions");
  const providerHint = $("provider-hint");
  const btnStart = $("btn-start");
  const btnCancel = $("btn-cancel");
  const bookSourceEl = $("book-source");
  const uploadRow = $("upload-row");
  const docFileEl = $("doc-file");
  const btnIngestPdf = $("btn-ingest-pdf");
  const uploadHint = $("upload-hint");
  let providerCatalog = [];
  let sourcesCatalog = [];
  let defaultBookSlug = "physics10-riazi-110209";
  let currentBookSlug = defaultBookSlug;
  const statusPanel = $("status-panel");
  const statusLabel = $("status-label");
  const statusDetail = $("status-detail");
  const logEl = $("log");
  const resultBox = $("result-box");
  const resultSummary = $("result-summary");
  const resultPaths = $("result-paths");
  const keyHint = $("key-hint");
  const previewList = $("preview-list");
  const previewEmpty = $("preview-empty");
  const btnReload = $("btn-reload-preview");
  const btnExportApproved = $("btn-export-approved");
  const approvedOnlyView = $("approved-only-view");
  const reviewSummaryEl = $("review-summary");

  let pollTimer = null;
  let currentJobId = null;
  let currentPrefix = null;
  const letters = ["الف", "ب", "ج", "د"];
  const statusFa = {
    pending: "در انتظار",
    approved: "تأیید",
    rejected: "رد",
    needs_review: "نیاز به بازبینی",
  };

  async function api(path, options) {
    const opts = { ...options };
    const headers = { Accept: "application/json", ...(opts.headers || {}) };
    if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    opts.headers = headers;
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail || res.statusText || "خطا";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function fillChapters(chapters) {
    chapterEl.innerHTML = "";
    (chapters || []).forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.number;
      opt.textContent = `${c.number}. ${c.title}`;
      chapterEl.appendChild(opt);
    });
  }

  function syncSourceUi() {
    const slug = bookSourceEl.value || defaultBookSlug;
    currentBookSlug = slug;
    const isUploadSlot = slug === "__upload__";
    uploadRow.classList.toggle("hidden", !isUploadSlot && !slug.startsWith("doc-"));
    if (isUploadSlot) {
      uploadRow.classList.remove("hidden");
      sampleEl.checked = false;
      sampleEl.disabled = true;
      fillChapters([]);
      uploadHint.textContent = "PDF را انتخاب کنید تا متن استخراج و فصل‌ها ساخته شوند.";
      uploadHint.className = "hint";
      return;
    }
    const src = sourcesCatalog.find((s) => s.book_slug === slug);
    fillChapters(src ? src.chapters : []);
    const builtin = slug === defaultBookSlug;
    sampleEl.disabled = !builtin;
    if (!builtin) sampleEl.checked = false;
    if (slug.startsWith("doc-")) {
      uploadRow.classList.remove("hidden");
      uploadHint.textContent = src
        ? `منبع فعال: ${src.title || slug}`
        : "می‌توانید PDF دیگری بخوانید.";
      uploadHint.className = "hint ok";
    } else {
      uploadRow.classList.add("hidden");
    }
    if (src && src.title) {
      $("book-title").textContent = src.title;
    }
  }

  function rebuildSourceSelect(selectedSlug) {
    bookSourceEl.innerHTML = "";
    sourcesCatalog.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.book_slug;
      const kind = s.kind === "builtin" ? "کتاب" : "PDF";
      opt.textContent = `${kind}: ${s.title || s.book_slug}`;
      bookSourceEl.appendChild(opt);
    });
    const uploadOpt = document.createElement("option");
    uploadOpt.value = "__upload__";
    uploadOpt.textContent = "PDF جدید…";
    bookSourceEl.appendChild(uploadOpt);
    const pick =
      selectedSlug ||
      (sourcesCatalog.find((s) => s.book_slug === defaultBookSlug) || sourcesCatalog[0] || {})
        .book_slug ||
      defaultBookSlug;
    if ([...bookSourceEl.options].some((o) => o.value === pick)) {
      bookSourceEl.value = pick;
    }
    syncSourceUi();
  }

  function setStatus(kind, label, detail) {
    statusPanel.className = `status-strip ${kind}`;
    statusLabel.textContent = label;
    statusDetail.textContent = detail || "";
  }

  function renderLog(logs) {
    logEl.textContent = (logs || []).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  function renderResult(job) {
    if (!job || job.status !== "succeeded") {
      resultBox.classList.add("hidden");
      return;
    }
    resultBox.classList.remove("hidden");
    resultSummary.textContent = `${job.question_count} سؤال آماده شد.`;
    resultPaths.innerHTML = "";
    Object.entries(job.paths || {}).forEach(([k, v]) => {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${k}</strong>: <code>${v}</code>`;
      resultPaths.appendChild(li);
    });
    if (job.metrics) {
      const m = job.metrics;
      const extra = document.createElement("li");
      let line =
        `پاسخ‌ها: ${JSON.stringify(m.answer_positions || {})} · ` +
        `سختی: ${JSON.stringify(m.difficulties || {})} · ` +
        `نیازبازبینی: ${m.needs_review ?? 0}`;
      if (m.calls_by_provider) {
        line += ` · provider: ${JSON.stringify(m.calls_by_provider)}`;
      }
      if (m.fallback_count) {
        line += ` · fallback: ${m.fallback_count}`;
        if (m.fallback_provider) line += `→${m.fallback_provider}`;
      }
      extra.textContent = line;
      resultPaths.appendChild(extra);
      if (m.pattern_pack_used) {
        const pp = document.createElement("li");
        pp.textContent =
          `بسته الگو: ${m.pattern_pack_status === "web" ? "از وب" : "بدون وب"} · ` +
          `${m.patterns_count} الگو · ${m.web_sources_count ?? 0} منبع · ` +
          `رد به‌خاطر شباهت به تست وب: ${m.web_dup_rejected ?? 0}` +
          (m.search_error ? ` · خطای جستجو: ${m.search_error}` : "");
        resultPaths.appendChild(pp);
      }
      if (m.fallback_count) {
        const warn = document.createElement("li");
        warn.style.color = "#b45309";
        warn.textContent =
          `هشدار: ${m.fallback_count} فراخوانی با fallback` +
          (m.fallback_reasons && m.fallback_reasons.length
            ? ` — ${m.fallback_reasons[0]}`
            : "");
        resultPaths.appendChild(warn);
      }
    }
  }

  function setBusy(busy) {
    btnStart.disabled = busy;
    btnCancel.disabled = !busy;
    chapterEl.disabled = busy;
    countEl.disabled = busy;
    const builtin = currentBookSlug === defaultBookSlug;
    sampleEl.disabled = busy || !builtin;
    verifyEl.disabled = busy;
    balanceEl.disabled = busy;
    if (styleMineEl) styleMineEl.disabled = busy || sampleEl.checked;
    mixEl.disabled = busy;
    if (patternMixEl) patternMixEl.disabled = busy;
    profileEl.disabled = busy;
    if (providerEl) providerEl.disabled = busy;
    if (modelEl) modelEl.disabled = busy;
    if (bookSourceEl) bookSourceEl.disabled = busy;
    if (docFileEl) docFileEl.disabled = busy;
    if (btnIngestPdf) btnIngestPdf.disabled = busy;
  }

  function applyProviderSelection(providerId, { keepModel } = { keepModel: false }) {
    const entry = providerCatalog.find((p) => p.id === providerId) || providerCatalog[0];
    if (!entry) return;
    modelListEl.innerHTML = "";
    (entry.model_suggestions || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      modelListEl.appendChild(opt);
    });
    if (!keepModel || !modelEl.value.trim()) {
      modelEl.value = entry.default_model || (entry.model_suggestions || [])[0] || "";
    }
    if (entry.has_api_key) {
      providerHint.textContent = `کلید ${entry.label} آماده است.`;
      providerHint.className = "hint ok";
    } else {
      providerHint.textContent = `کلید API برای ${entry.label} نیست — یا provider دیگر را انتخاب کنید یا حالت نمونه.`;
      providerHint.className = "hint warn";
    }
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshJob(jobId) {
    const job = await api(`/api/jobs/${jobId}`);
    renderLog(job.logs);
    if (job.status === "queued" || job.status === "running") {
      setBusy(true);
      setStatus("running", "در حال تولید…", `فصل ${job.chapter} · ${job.count} سؤال`);
    } else if (job.status === "succeeded") {
      setBusy(false);
      stopPoll();
      setStatus("ok", "تمام شد", `${job.question_count} سؤال`);
      renderResult(job);
      if (job.prefix) {
        currentPrefix = job.prefix;
        btnReload.disabled = false;
        btnExportApproved.disabled = false;
        await loadPreview(job.prefix);
      }
    } else if (job.status === "failed") {
      setBusy(false);
      stopPoll();
      setStatus("err", "خطا", job.error || "نامشخص");
      renderResult(null);
    } else if (job.status === "cancelled") {
      setBusy(false);
      stopPoll();
      setStatus("idle", "لغو شد", "");
      renderResult(null);
    }
    return job;
  }

  function startPoll(jobId) {
    stopPoll();
    currentJobId = jobId;
    pollTimer = setInterval(() => {
      refreshJob(jobId).catch((err) => {
        setStatus("err", "خطای وضعیت", err.message);
        stopPoll();
        setBusy(false);
      });
    }, 900);
  }

  async function loadPreview(prefix) {
    currentPrefix = prefix;
    const approvedOnly = approvedOnlyView.checked;
    const data = await api(
      `/api/exports/${encodeURIComponent(prefix)}?approved_only=${approvedOnly ? "true" : "false"}`
    );
    previewEmpty.classList.add("hidden");
    previewList.innerHTML = "";
    const rs = data.review_summary || {};
    reviewSummaryEl.textContent =
      `بازبینی — در انتظار: ${rs.pending || 0} · تأیید: ${rs.approved || 0} · ` +
      `رد: ${rs.rejected || 0} · نیازبازبینی: ${rs.needs_review || 0}`;

    (data.questions || []).forEach((q, i) => {
      const article = document.createElement("article");
      article.className = "q";
      const opts = (q.options || [])
        .map((o, j) => {
          const mark = j === q.correct_index ? " ✓" : "";
          return `<li class="${j === q.correct_index ? "correct" : ""}">${letters[j]}) ${escapeHtml(o)}${mark}</li>`;
        })
        .join("");
      const rev = q.review_status || "pending";
      const verifyNote = (q.review_status === "needs_review" || q.verifier_agrees === false)
        ? `<p class="verify warn"><strong>نیاز به بازبینی:</strong> مدل تأییدکننده «${letters[q.verifier_answer_index] || "?"}» گفت. ${escapeHtml(q.verifier_reason || "")}</p>`
        : "";
      article.innerHTML = `
        <header>
          <span class="q-num">${i + 1}</span>
          <span class="pill">${escapeHtml(q.difficulty || "")}</span>
          <span class="pill">${escapeHtml(PATTERN_FA[q.pattern] || q.pattern || "")}</span>
          <span class="pill status-${rev}">${statusFa[rev] || rev}</span>
          <span class="muted q-id">${escapeHtml(q.id || "")}</span>
        </header>
        <p class="stem">${escapeHtml(q.stem)}</p>
        <ul class="opts">${opts}</ul>
        <p class="expl"><strong>توضیح:</strong> ${escapeHtml(q.explanation || "")}</p>
        ${verifyNote}
        <details>
          <summary>نقل‌قول کتاب</summary>
          <blockquote>${escapeHtml((q.source_refs || []).join(" / ") || "—")}</blockquote>
        </details>
        <div class="q-actions">
          <button type="button" class="btn tiny ok" data-act="approve">تأیید</button>
          <button type="button" class="btn tiny danger" data-act="reject">رد</button>
          <button type="button" class="btn tiny" data-act="edit">ویرایش</button>
        </div>
        <div class="edit-panel hidden">
          <label>صورت سؤال<textarea class="edit-stem">${escapeHtml(q.stem)}</textarea></label>
          <label>گزینه الف<input class="edit-opt" data-i="0" value="${escapeAttr(q.options[0] || "")}" /></label>
          <label>گزینه ب<input class="edit-opt" data-i="1" value="${escapeAttr(q.options[1] || "")}" /></label>
          <label>گزینه ج<input class="edit-opt" data-i="2" value="${escapeAttr(q.options[2] || "")}" /></label>
          <label>گزینه د<input class="edit-opt" data-i="3" value="${escapeAttr(q.options[3] || "")}" /></label>
          <label>گزینه درست
            <select class="edit-correct">
              ${letters.map((l, j) => `<option value="${j}" ${j === q.correct_index ? "selected" : ""}>${l}</option>`).join("")}
            </select>
          </label>
          <label>توضیح<textarea class="edit-expl">${escapeHtml(q.explanation || "")}</textarea></label>
          <label>سختی
            <select class="edit-diff">
              ${["easy", "medium", "hard"].map((d) => `<option value="${d}" ${d === q.difficulty ? "selected" : ""}>${d}</option>`).join("")}
            </select>
          </label>
          <button type="button" class="btn primary tiny" data-act="save">ذخیره ویرایش</button>
        </div>
      `;
      article.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => onQuestionAction(prefix, q.id, btn.dataset.act, article));
      });
      previewList.appendChild(article);
    });
  }

  async function onQuestionAction(prefix, qid, act, article) {
    try {
      if (act === "approve" || act === "reject") {
        await api(`/api/exports/${encodeURIComponent(prefix)}/questions/${encodeURIComponent(qid)}`, {
          method: "PATCH",
          body: JSON.stringify({ review_status: act === "approve" ? "approved" : "rejected" }),
        });
        await loadPreview(prefix);
      } else if (act === "edit") {
        article.querySelector(".edit-panel").classList.toggle("hidden");
      } else if (act === "save") {
        const panel = article.querySelector(".edit-panel");
        const options = [...panel.querySelectorAll(".edit-opt")]
          .sort((a, b) => Number(a.dataset.i) - Number(b.dataset.i))
          .map((el) => el.value);
        await api(`/api/exports/${encodeURIComponent(prefix)}/questions/${encodeURIComponent(qid)}`, {
          method: "PATCH",
          body: JSON.stringify({
            stem: panel.querySelector(".edit-stem").value,
            options,
            correct_index: Number(panel.querySelector(".edit-correct").value),
            explanation: panel.querySelector(".edit-expl").value,
            difficulty: panel.querySelector(".edit-diff").value,
          }),
        });
        await loadPreview(prefix);
      }
    } catch (err) {
      alert(err.message);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    resultBox.classList.add("hidden");
    try {
      setBusy(true);
      setStatus("running", "شروع…", "");
      renderLog([]);
      if (!currentBookSlug || currentBookSlug === "__upload__") {
        throw new Error("ابتدا یک PDF بخوانید یا منبع را انتخاب کنید.");
      }
      if (!chapterEl.value) {
        throw new Error("فصلی برای این منبع موجود نیست.");
      }
      const body = {
        chapter: Number(chapterEl.value),
        count: Number(countEl.value),
        sample: sampleEl.checked,
        book_slug: currentBookSlug,
        verify: verifyEl.checked,
        style_mine: !!(styleMineEl && styleMineEl.checked && !sampleEl.checked),
        balance_answers: balanceEl.checked,
        difficulty_mix: mixEl.value.trim() || null,
        pattern_mix: patternMixEl && patternMixEl.value.trim() ? patternMixEl.value.trim() : null,
        export_profile: profileEl.value || null,
        llm_provider: providerEl && providerEl.value ? providerEl.value : null,
        llm_model: modelEl && modelEl.value.trim() ? modelEl.value.trim() : null,
        use_pattern_pack: !!($("use-pattern-pack") && $("use-pattern-pack").checked && !sampleEl.checked),
      };
      const job = await api("/api/generate", { method: "POST", body: JSON.stringify(body) });
      currentJobId = job.id;
      renderLog(job.logs);
      startPoll(job.id);
      await refreshJob(job.id);
    } catch (err) {
      setBusy(false);
      setStatus("err", "شروع نشد", err.message);
    }
  });

  btnCancel.addEventListener("click", async () => {
    try {
      await api("/api/cancel", { method: "POST", body: "{}" });
      if (currentJobId) await refreshJob(currentJobId);
    } catch (err) {
      setStatus("err", "لغو ناموفق", err.message);
    }
  });

  btnReload.addEventListener("click", () => {
    if (currentPrefix) loadPreview(currentPrefix).catch((e) => alert(e.message));
  });
  approvedOnlyView.addEventListener("change", () => {
    if (currentPrefix) loadPreview(currentPrefix).catch((e) => alert(e.message));
  });
  btnExportApproved.addEventListener("click", async () => {
    if (!currentPrefix) return;
    try {
      const data = await api(`/api/exports/${encodeURIComponent(currentPrefix)}/export-approved`, {
        method: "POST",
        body: "{}",
      });
      alert(`خروجی تأییدشده‌ها: ${data.count} سؤال\n${JSON.stringify(data.paths, null, 2)}`);
    } catch (err) {
      alert(err.message);
    }
  });

  bookSourceEl.addEventListener("change", () => {
    syncSourceUi();
    document.dispatchEvent(new CustomEvent("darsamooz:source-changed"));
  });
  chapterEl.addEventListener("change", () => {
    document.dispatchEvent(new CustomEvent("darsamooz:source-changed"));
  });

  btnIngestPdf.addEventListener("click", async () => {
    const file = docFileEl.files && docFileEl.files[0];
    if (!file) {
      uploadHint.textContent = "ابتدا یک فایل PDF انتخاب کنید.";
      uploadHint.className = "hint warn";
      return;
    }
    try {
      btnIngestPdf.disabled = true;
      setStatus("running", "خواندن PDF…", file.name);
      uploadHint.textContent = "در حال استخراج متن…";
      uploadHint.className = "hint";
      const fd = new FormData();
      fd.append("file", file, file.name);
      const data = await api("/api/documents", { method: "POST", body: fd });
      const docs = await api("/api/documents");
      sourcesCatalog = docs.sources || [];
      rebuildSourceSelect(data.book_slug);
      currentBookSlug = data.book_slug;
      bookSourceEl.value = data.book_slug;
      syncSourceUi();
      fillChapters(data.chapters || []);
      const nCh = (data.chapters || []).length;
      uploadHint.textContent = `خوانده شد: ${nCh} فصل · ${data.title || data.book_slug}`;
      uploadHint.className = "hint ok";
      setStatus("ok", "PDF خوانده شد", `${nCh} فصل آماده تولید`);
      document.dispatchEvent(
        new CustomEvent("darsamooz:ingested", { detail: { book_slug: data.book_slug, chapters: data.chapters || [] } })
      );
    } catch (err) {
      uploadHint.textContent = err.message;
      uploadHint.className = "hint warn";
      setStatus("err", "خواندن PDF ناموفق", err.message);
    } finally {
      btnIngestPdf.disabled = false;
    }
  });

  async function boot() {
    try {
      const meta = await api("/api/meta");
      $("book-title").textContent = meta.book_title;
      $("version-badge").textContent = `v${meta.version}`;
      const modelBit = meta.model ? ` · ${meta.model}` : "";
      const fbBit = meta.fallback_provider ? ` → fallback:${meta.fallback_provider}` : "";
      $("provider-badge").textContent = `${meta.provider}${modelBit}${fbBit}`;
      defaultBookSlug = meta.default_book_slug || "physics10-riazi-110209";
      sourcesCatalog = meta.sources || [
        {
          book_slug: defaultBookSlug,
          title: meta.book_title,
          kind: "builtin",
          chapters: meta.chapters || [],
        },
      ];
      rebuildSourceSelect(defaultBookSlug);
      profileEl.innerHTML = `<option value="">— بدون خروجی پروفایلی —</option>`;
      (meta.export_profiles || []).forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.id} — ${p.name || p.label || ""}`;
        profileEl.appendChild(opt);
      });

      providerCatalog = meta.providers || [];
      providerEl.innerHTML = "";
      providerCatalog.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.label}${p.has_api_key ? "" : " (بدون کلید)"}`;
        if (p.is_default) opt.selected = true;
        providerEl.appendChild(opt);
      });
      const selected =
        providerCatalog.find((p) => p.is_default)?.id ||
        providerCatalog.find((p) => p.has_api_key)?.id ||
        (providerCatalog[0] && providerCatalog[0].id);
      if (selected) {
        providerEl.value = selected;
        applyProviderSelection(selected);
      }
      providerEl.addEventListener("change", () => {
        applyProviderSelection(providerEl.value);
      });

      if (meta.has_api_key || providerCatalog.some((p) => p.has_api_key)) {
        keyHint.textContent = "حداقل یک کلید API پیدا شد؛ provider و مدل را انتخاب کنید.";
        keyHint.className = "hint ok";
      } else {
        keyHint.textContent =
          "کلید API نیست — فقط حالت نمونه کار می‌کند. کلید را در .env بگذارید.";
        keyHint.className = "hint warn";
        sampleEl.checked = true;
      }
      verifyEl.checked = meta.verify_default !== false;

      if (styleMineHint) {
        const ready =
          meta.style_mine_ready !== undefined
            ? meta.style_mine_ready
            : meta.has_api_key;
        if (ready) {
          styleMineHint.textContent =
            "با همان کلید LLM: ترجیحاً OpenRouter برای جستجوی وب؛ محتوا فقط از متن منبع.";
          styleMineHint.className = "hint ok";
        } else {
          styleMineHint.textContent =
            "برای الگوبرداری وب، کلید OpenRouter/Gemini/OpenAI را در .env بگذارید.";
          styleMineHint.className = "hint warn";
          if (styleMineEl) styleMineEl.checked = false;
        }
      }
      if (styleMineEl) {
        styleMineEl.addEventListener("change", () => {
          if (styleMineEl.checked && sampleEl.checked) {
            sampleEl.checked = false;
          }
        });
      }
      sampleEl.addEventListener("change", () => {
        if (sampleEl.checked && styleMineEl) {
          styleMineEl.checked = false;
          styleMineEl.disabled = true;
        } else if (styleMineEl) {
          styleMineEl.disabled = false;
        }
      });

      const active = await api("/api/jobs/active");
      if (active.job && (active.job.status === "running" || active.job.status === "queued")) {
        currentJobId = active.job.id;
        if (active.job.book_slug) {
          currentBookSlug = active.job.book_slug;
          if ([...bookSourceEl.options].some((o) => o.value === currentBookSlug)) {
            bookSourceEl.value = currentBookSlug;
            syncSourceUi();
          }
        }
        setBusy(true);
        renderLog(active.job.logs);
        startPoll(active.job.id);
      } else if (active.job && active.job.status === "succeeded") {
        renderLog(active.job.logs);
        setStatus("ok", "آخرین اجرا موفق", "");
        renderResult(active.job);
        if (active.job.prefix) {
          currentPrefix = active.job.prefix;
          btnReload.disabled = false;
          btnExportApproved.disabled = false;
          await loadPreview(active.job.prefix);
        }
      } else {
        setStatus("idle", "آماده", "منبع، فصل و تعداد را انتخاب کنید.");
      }
    } catch (err) {
      setStatus("err", "بارگذاری نشد", err.message);
    }
  }

  window.DarsAmooz = {
    api,
    currentSource: () => ({
      slug: currentBookSlug,
      chapter: chapterEl.value ? Number(chapterEl.value) : null,
      provider: providerEl && providerEl.value ? providerEl.value : null,
      model: modelEl && modelEl.value.trim() ? modelEl.value.trim() : null,
    }),
  };

  boot().then(() => document.dispatchEvent(new CustomEvent("darsamooz:source-changed")));
})();
