(() => {
  const $ = (id) => document.getElementById(id);
  const TYPE_FA = { tarifi: "تعریفی", mafhumi: "مفهومی", halli: "حلی/عددی", tarkibi: "ترکیبی" };
  const DIFF_FA = { easy: "آسان", medium: "متوسط", hard: "سخت" };

  const els = {
    sourceLine: $("handout-source-line"),
    status: $("handout-status"),
    topicBadge: $("topic-badge"),
    topicForm: $("topic-form"),
    btnAnalyze: $("btn-analyze"),
    btnTopicSave: $("btn-topic-save"),
    summary: $("tp-summary"),
    webBadge: $("web-badge"),
    backend: $("web-backend"),
    btnMine: $("btn-mine"),
    webError: $("web-error"),
    webErrorText: $("web-error-text"),
    btnLlmOnly: $("btn-llm-only"),
    sources: $("web-sources"),
    packBadge: $("pack-badge"),
    packMeta: $("pack-meta"),
    patternList: $("pattern-list"),
    packJson: $("pack-json"),
    btnPackJson: $("btn-pack-json"),
    btnPackSave: $("btn-pack-save"),
    usePack: $("use-pattern-pack"),
    usePackHint: $("use-pack-hint"),
  };
  const fields = {
    subject: $("tp-subject"),
    grade: $("tp-grade"),
    track: $("tp-track"),
    book: $("tp-book"),
    chapter: $("tp-chapter"),
    topics: $("tp-topics"),
    key_concepts: $("tp-concepts"),
    exam_targets: $("tp-exams"),
    search_queries: $("tp-queries"),
  };
  let state = { topic: null, sources: null, pack: null };
  let busy = false;

  const api = (...a) => window.DarsAmooz.api(...a);
  const src = () => (window.DarsAmooz ? window.DarsAmooz.currentSource() : { slug: null, chapter: null });
  const esc = (s) =>
    String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const splitList = (v) => String(v || "").split(/[،,]/).map((x) => x.trim()).filter(Boolean);
  const splitLines = (v) => String(v || "").split(/\n/).map((x) => x.trim()).filter(Boolean);

  function say(msg, kind) {
    els.status.textContent = msg || "";
    els.status.className = "hint" + (kind ? ` ${kind}` : "");
  }

  function setBusy(b) {
    busy = b;
    els.btnAnalyze.disabled = b || !validSource();
    els.btnMine.disabled = b || !state.topic;
    els.btnLlmOnly.disabled = b;
    els.btnTopicSave.disabled = b;
    els.btnPackSave.disabled = b || !state.pack;
    els.btnPackJson.disabled = b || !state.pack;
  }

  function validSource() {
    const s = src();
    return !!(s.slug && s.slug !== "__upload__" && s.chapter);
  }

  function base() {
    const s = src();
    return `/api/handout/${encodeURIComponent(s.slug)}/${s.chapter}`;
  }

  function renderTopic() {
    const t = state.topic;
    if (!t) {
      els.topicForm.classList.add("hidden");
      els.topicBadge.textContent = "تشخیص داده نشده";
      els.topicBadge.className = "badge";
      return;
    }
    els.topicForm.classList.remove("hidden");
    fields.subject.value = t.subject || "";
    fields.grade.value = t.grade || "";
    fields.track.value = t.track || "";
    fields.book.value = t.book || "";
    fields.chapter.value = t.chapter || "";
    fields.topics.value = (t.topics || []).join("، ");
    fields.key_concepts.value = (t.key_concepts || []).join("، ");
    fields.exam_targets.value = (t.exam_targets || []).join("، ");
    fields.search_queries.value = (t.search_queries || []).join("\n");
    const conf = typeof t.confidence === "number" ? ` · اطمینان مدل: ${Math.round(t.confidence * 100)}٪` : "";
    els.summary.textContent = (t.summary_fa || "") + conf;
    els.topicBadge.textContent = t.confirmed ? "تأیید شده" : "تشخیص مدل — بررسی و تأیید کنید";
    els.topicBadge.className = "badge " + (t.confirmed ? "ok" : "warn");
  }

  function renderSources() {
    const ws = state.sources;
    els.sources.innerHTML = "";
    els.webError.classList.add("hidden");
    if (!ws) {
      els.webBadge.textContent = "جستجو نشده";
      els.webBadge.className = "badge";
      return;
    }
    if (ws.ok === false && ws.error) {
      els.webBadge.textContent = "جستجو ناموفق";
      els.webBadge.className = "badge err";
      els.webError.classList.remove("hidden");
      const attempts = (ws.attempts || []).map((a) => `${a.backend}: ${a.ok ? "ok" : a.error}`).join(" | ");
      els.webErrorText.textContent = `خطا: ${ws.error}${attempts ? " — " + attempts : ""}`;
      return;
    }
    if (ws.skipped) {
      els.webBadge.textContent = "بدون وب (انتخاب کاربر)";
      els.webBadge.className = "badge warn";
      return;
    }
    const list = ws.sources || [];
    els.webBadge.textContent = `${list.length} منبع · ${ws.excerpt_count || 0} نمونه تست · ${ws.backend || "?"}`;
    els.webBadge.className = "badge ok";
    list.forEach((s) => {
      const li = document.createElement("li");
      const ex = (s.excerpts || []).map((e) => `<li>${esc(e)}</li>`).join("");
      li.innerHTML =
        `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a>` +
        `<span class="src-host" dir="ltr">${esc((() => { try { return new URL(s.url).host; } catch { return s.url; } })())}</span>` +
        (s.snippet ? `<p class="src-snippet">${esc(s.snippet)}</p>` : "") +
        `<details><summary>${(s.excerpts || []).length} نمونه تست خوانده‌شده (فقط برای الگو؛ کپی نمی‌شود) · ${esc(s.fetch_status || "")}</summary><ul>${ex}</ul></details>`;
      els.sources.appendChild(li);
    });
  }

  function renderPack() {
    const p = state.pack;
    els.patternList.innerHTML = "";
    els.packJson.classList.add("hidden");
    if (!p) {
      els.packBadge.textContent = "—";
      els.packBadge.className = "badge";
      els.packMeta.textContent = "";
      els.usePack.checked = false;
      els.usePack.disabled = true;
      els.usePackHint.textContent = "(بسته الگو ندارد)";
      setBusy(busy);
      return;
    }
    const web = p.status === "web";
    els.packBadge.textContent = `${(p.patterns || []).length} الگو · ${web ? "از تست‌های وب" : "بدون وب"}`;
    els.packBadge.className = "badge " + (web ? "ok" : "warn");
    els.packMeta.textContent =
      (p.exam_styles && p.exam_styles.length ? `سبک آزمون: ${p.exam_styles.join("، ")} · ` : "") +
      `منابع: ${p.sources_count || 0}` +
      (p.note ? ` · ${p.note}` : "");
    (p.patterns || []).forEach((pt, i) => {
      const card = document.createElement("article");
      card.className = "pattern-card";
      const ev = (pt.evidence_urls || [])
        .map((u, k) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">منبع ${k + 1}</a>`)
        .join(" ");
      card.innerHTML = `
        <header>
          <label class="check compact"><input type="checkbox" data-i="${i}" class="pt-enabled" ${pt.enabled !== false ? "checked" : ""}/> <strong>${esc(pt.id)} · ${esc(pt.name_fa)}</strong></label>
          <span class="badge">${esc(TYPE_FA[pt.question_type] || pt.question_type)}</span>
          <span class="badge">${esc(DIFF_FA[pt.difficulty] || pt.difficulty)}</span>
          ${pt.numeric ? '<span class="badge">عددی</span>' : ""}
        </header>
        <label class="field"><span>اسکلت صورت سؤال</span><textarea rows="2" data-i="${i}" class="pt-template">${esc(pt.stem_template)}</textarea></label>
        <p><b>شکل گزینه‌ها:</b> ${esc(pt.option_format || "—")}</p>
        <p><b>ساخت گزینه‌های غلط:</b> ${esc((pt.distractor_strategies || []).join("؛ ") || "—")}</p>
        <p><b>دام‌ها:</b> ${esc((pt.traps || []).join("؛ ") || "—")}</p>
        <p class="hint">${ev || "بدون شاهد وب"}</p>`;
      els.patternList.appendChild(card);
    });
    els.usePack.disabled = false;
    els.usePack.checked = true;
    els.usePackHint.textContent = `(${(p.patterns || []).length} الگو${web ? "" : " — بدون وب"})`;
    setBusy(busy);
  }

  function collectPackEdits() {
    if (!els.packJson.classList.contains("hidden")) {
      return JSON.parse(els.packJson.value);
    }
    const p = JSON.parse(JSON.stringify(state.pack));
    els.patternList.querySelectorAll(".pt-enabled").forEach((cb) => {
      p.patterns[Number(cb.dataset.i)].enabled = cb.checked;
    });
    els.patternList.querySelectorAll(".pt-template").forEach((ta) => {
      p.patterns[Number(ta.dataset.i)].stem_template = ta.value.trim();
    });
    return p;
  }

  async function load() {
    if (!validSource()) {
      els.sourceLine.textContent = "منبع و فصل را بالا انتخاب کنید (یا PDF جزوه را بخوانید).";
      state = { topic: null, sources: null, pack: null };
      renderTopic(); renderSources(); renderPack(); setBusy(false);
      return;
    }
    const s = src();
    els.sourceLine.textContent = `منبع: ${s.slug} · فصل ${s.chapter}`;
    try {
      const data = await api(base());
      state = { topic: data.topic_profile, sources: data.web_sources, pack: data.pattern_pack };
    } catch (err) {
      state = { topic: null, sources: null, pack: null };
      say(err.message, "warn");
    }
    renderTopic(); renderSources(); renderPack(); setBusy(false);
  }

  async function analyze() {
    if (!validSource()) return;
    setBusy(true);
    say("در حال خواندن متن و تشخیص موضوع…");
    try {
      const s = src();
      const data = await api(`${base()}/analyze`, {
        method: "POST",
        body: JSON.stringify({ llm_provider: s.provider, llm_model: s.model }),
      });
      state.topic = data.topic_profile;
      renderTopic();
      say("موضوع تشخیص داده شد. بررسی و در صورت نیاز اصلاح کنید، بعد «ذخیره و تأیید».", "ok");
    } catch (err) {
      say(err.message, "warn");
    } finally {
      setBusy(false);
    }
  }

  async function saveTopic() {
    setBusy(true);
    try {
      const body = {
        subject: fields.subject.value.trim(),
        grade: fields.grade.value.trim(),
        track: fields.track.value.trim(),
        book: fields.book.value.trim(),
        chapter: fields.chapter.value.trim(),
        topics: splitList(fields.topics.value),
        key_concepts: splitList(fields.key_concepts.value),
        exam_targets: splitList(fields.exam_targets.value),
        search_queries: splitLines(fields.search_queries.value),
        confirm: true,
      };
      const data = await api(`${base()}/topic`, { method: "PUT", body: JSON.stringify(body) });
      state.topic = data.topic_profile;
      renderTopic();
      say("موضوع تأیید شد. حالا «جستجو و استخراج الگو».", "ok");
    } catch (err) {
      say(err.message, "warn");
    } finally {
      setBusy(false);
    }
  }

  async function mine(llmOnly) {
    setBusy(true);
    say(llmOnly ? "ساخت الگو بدون وب…" : "جستجوی وب، خواندن صفحه‌ها و استخراج الگو… (ممکن است یک‌دو دقیقه طول بکشد)");
    try {
      const s = src();
      const data = await api(`${base()}/mine`, {
        method: "POST",
        body: JSON.stringify({
          backend: els.backend.value,
          llm_only: !!llmOnly,
          llm_provider: s.provider,
          llm_model: s.model,
        }),
      });
      if (!data.ok && data.search_failed) {
        state.sources = { ok: false, error: data.error, attempts: data.attempts };
        renderSources();
        say("جستجوی وب ناموفق بود — جزئیات بالا.", "warn");
        return;
      }
      state.pack = data.pattern_pack;
      state.sources = data.web_sources;
      renderSources(); renderPack();
      say(`آماده: ${(state.pack.patterns || []).length} الگو. حالا می‌توانید تولید را شروع کنید.`, "ok");
    } catch (err) {
      say(err.message, "warn");
    } finally {
      setBusy(false);
    }
  }

  async function savePack() {
    let edited;
    try {
      edited = collectPackEdits();
    } catch (err) {
      say(`JSON نامعتبر: ${err.message}`, "warn");
      return;
    }
    setBusy(true);
    try {
      const data = await api(`${base()}/patterns`, { method: "PUT", body: JSON.stringify(edited) });
      state.pack = data.pattern_pack;
      renderPack();
      say("الگوها ذخیره شد.", "ok");
    } catch (err) {
      say(err.message, "warn");
    } finally {
      setBusy(false);
    }
  }

  els.btnAnalyze.addEventListener("click", analyze);
  els.btnTopicSave.addEventListener("click", saveTopic);
  els.btnMine.addEventListener("click", () => mine(false));
  els.btnLlmOnly.addEventListener("click", () => mine(true));
  els.btnPackSave.addEventListener("click", savePack);
  els.btnPackJson.addEventListener("click", () => {
    if (!state.pack) return;
    const show = els.packJson.classList.contains("hidden");
    if (show) els.packJson.value = JSON.stringify(collectPackEdits(), null, 2);
    els.packJson.classList.toggle("hidden", !show);
    els.patternList.classList.toggle("hidden", show);
  });

  document.addEventListener("darsamooz:source-changed", () => load());
  document.addEventListener("darsamooz:ingested", async () => {
    await load();
    if (validSource() && !state.topic) await analyze();
  });
})();
