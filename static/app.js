/* Goulash · Поставщики — фронт на Alpine.js без сборки.
   Все данные из API выводятся через x-text (не x-html), чтобы пользовательский текст не исполнялся. */

const api = async (path, opts = {}) => {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Ошибка ${res.status}`);
  }
  return data;
};

const money = (v) => (v == null ? "—" : Math.round(v).toLocaleString("ru-RU"));
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("ru-RU") : "");
const fmtDateTime = (s) => (s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");
const scoreClass = (v) => (v >= 75 ? "" : v >= 50 ? "mid" : "low");
const plural = (n, one, few, many) => {
  const m = Math.abs(n) % 100, d = m % 10;
  if (m > 10 && m < 20) return many;
  return d === 1 ? one : d >= 2 && d <= 4 ? few : many;
};

const DOC_LABELS = {
  declaration: "Декларация ЕАЭС", certificate: "Сертификат соответствия", mercury: "Меркурий (ВСД)",
  haccp: "ХАССП", chestny_znak: "Честный знак", halal: "Халяль",
};
const CHANNELS = { whatsapp: "WhatsApp", email: "E-mail", telegram: "Telegram", copy: "Скопировать" };
const PART_LABELS = { match: "Соответствие запросу", reliability: "Надёжность", price: "Цена", logistics: "Логистика" };

// Leaflet держим вне реактивного состояния Alpine.
let map = null, markersLayer = null, markersById = {};
let collectionSaveQueue = Promise.resolve();
const queuedCollectionKeys = new Set();

document.addEventListener("alpine:init", () => {
  Alpine.data("app", () => ({
    view: "search",
    meta: { categories: [], cities: [], conditions: [], modes: [], examples: [], weights: {} },
    profile: null,
    realCase: null,
    realLoading: false,
    realError: "",
    realSelected: [],
    realDraft: null,
    realDraftLoading: false,
    query: "",
    criteria: null,
    filters: { max_distance_km: null, fit_min_order: false, hide_critical: true, supplier_type: "any" },
    mode: "balance",
    sort: "score",
    layout: "both",
    res: null,
    loading: false,
    error: "",
    filtersOpen: false,
    hlId: null,
    selected: [],          // [{id, name, preferred_channel}]
    detail: null,          // {supplier, notes, reliability, scored}
    factTab: "all",
    noteText: "",
    compare: null,
    kp: null,              // {subject, body, rows: [{supplier, channel, sent}], criteria}
    collections: [],
    collectionsLoading: false,
    collectionsError: "",
    openCollectionId: null,
    collectionChecked: {},
    replyEditor: { itemId: null, collectionId: null, price: "", terms: "", note: "", busy: false },
    collectionComparison: null,
    collectionDraft: null,
    pendingSaves: 0,
    saveError: "",
    _demoCollectionKey: crypto.randomUUID(),
    _realCollectionKey: crypto.randomUUID(),
    _demoSignature: null,
    _demoCollectionId: null,
    _realCollectionId: null,
    _searchVersion: 0,
    _submitVersion: 0,
    profileForm: null,
    confirmReset: false,
    toastText: "",
    mapOffline: false,
    _searchTimer: null,

    async init() {
      if (window.matchMedia("(max-width: 900px)").matches) this.layout = "list";
      try {
        await Promise.all([
          api("/meta").then(data => { this.meta = data; }),
          api("/profile").then(data => { this.profile = data; }),
          this.loadCollections(),
        ]);
      } catch (e) { this.error = "Не удалось загрузить сервис: " + e.message; }
      this.$watch("layout", () => this.$nextTick(() => this.refreshMap()));
      this.$watch("view", (v) => {
        history.replaceState(null, "", v === "real" ? "#real-case" : v === "collections" ? "#collections" : location.pathname + location.search);
        if (v === "collections") this.loadCollections().catch(e => this.toast(e.message));
        this.$nextTick(() => {
          window.scrollTo({ top: 0, behavior: "instant" });
          if (v === "search") this.refreshMap();
        });
      });
      if (location.hash === "#real-case") await this.openRealCase();
      else if (location.hash === "#collections") this.view = "collections";
    },

    // --- реальный сценарий: опубликованные факты и отдельные черновики ---
    async openRealCase() {
      this.view = "real";
      if (this.realCase || this.realLoading) return;
      this.realLoading = true; this.realError = "";
      try {
        this.realCase = await api("/real-case");
        this.realSelected = this.realCase.suppliers.filter(s => s.decision === "shortlist" || s.decision === "clarify").slice(0, 3).map(s => s.id);
      } catch (e) { this.realError = "Не удалось загрузить реальный пример: " + e.message; }
      finally { this.realLoading = false; }
    },
    realSource(s, id) { return s.sources.find(source => source.id === id); },
    realFact(s, key) {
      return [...s.company_facts, ...s.facts].find(f => f.key === key)
        || { label: key === "product_history" ? "История качества" : key, value: null, source_ids: [], missing_reason: "В просмотренных источниках не проверено." };
    },
    toggleRealSupplier(id) {
      if (this.realSelected.includes(id)) this.realSelected = this.realSelected.filter(x => x !== id);
      else if (this.realSelected.length < 3) this.realSelected.push(id);
      else { this.toast("Для сравнения можно выбрать до 3 компаний"); return; }
      this.persistSelection("real", this.realSelected).catch(e => this.toast(e.message));
    },
    get realCompared() { return this.realCase?.suppliers.filter(s => this.realSelected.includes(s.id)) || []; },
    async prepareRealDraft(s) {
      if (this.realDraftLoading) return;
      this.realDraftLoading = true;
      try {
        await this.persistSelection("real", [...new Set([...this.realSelected, s.id])]);
        const draft = await api(`/real-case/${s.id}/draft`, { method: "POST" });
        this.realDraft = { ...draft, supplier: s };
        this.$nextTick(() => {
          const section = document.getElementById("real-draft");
          section?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
          section?.focus({ preventScroll: true });
        });
      } catch (e) { this.toast(e.message); }
      finally { this.realDraftLoading = false; }
    },
    get realMailLink() {
      if (!this.realDraft?.supplier.email) return "";
      return `mailto:${this.realDraft.supplier.email}?subject=${encodeURIComponent(this.realDraft.subject)}&body=${encodeURIComponent(this.realDraft.body)}`;
    },
    async copyRealDraft() {
      try {
        await navigator.clipboard.writeText(`${this.realDraft.subject}\n\n${this.realDraft.body}`);
        this.toast("Запрос скопирован. Отправьте его поставщику в удобном канале.");
      } catch { this.toast("Не удалось скопировать. Выделите текст запроса вручную."); }
    },

    // --- справочники ---
    cityName(key) { return this.meta.cities.find((c) => c.key === key)?.name || key || ""; },
    catName(key) { return this.meta.categories.find((c) => c.key === key)?.label || key; },
    condLabel(key) { return this.meta.conditions.find((c) => c.key === key)?.label || key; },
    productName() {
      const o = this.res?.results.find((r) => r.offer && r.offer.key === this.criteria?.product)?.offer;
      return o?.name || this.meta.products?.find(p => p.key === this.criteria?.product)?.name || this.criteria?.product;
    },
    supWord(n) { return plural(n, "поставщик", "поставщика", "поставщиков"); },


    // --- поиск ---
    async submit(text) {
      if (text != null) this.query = text;
      if (!this.query.trim()) return;
      this.error = "";
      const version = ++this._submitVersion;
      ++this._searchVersion;
      clearTimeout(this._searchTimer);
      this.selected = [];
      this._demoCollectionKey = crypto.randomUUID(); this._demoCollectionId = null; this._demoSignature = null;
      this.saveError = "";
      this.loading = true;
      try {
        const criteria = await api("/parse", { method: "POST", body: { text: this.query } });
        if (version !== this._submitVersion) return;
        this.criteria = criteria;
        this.filters = { max_distance_km: null, fit_min_order: false, hide_critical: true, supplier_type: "any" };
        await this.search();
      } catch (e) { if (version === this._submitVersion) this.error = e.message; }
      finally { if (version === this._submitVersion) this.loading = false; }
    },
    scheduleSearch() {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this.search(), 250);
    },
    async search() {
      if (!this.criteria) return;
      const version = ++this._searchVersion;
      this.loading = true;
      try {
        const result = await api("/search", {
          method: "POST",
          body: { criteria: this.criteria, filters: this.filters, mode: this.mode, sort: this.sort },
        });
        if (version !== this._searchVersion) return;
        this.res = result;
        this.error = "";
        this.$nextTick(() => this.refreshMap());
      } catch (e) { if (version === this._searchVersion) this.error = e.message; }
      finally { if (version === this._searchVersion) this.loading = false; }
    },
    reset() {
      ++this._submitVersion; ++this._searchVersion;
      clearTimeout(this._searchTimer);
      this.loading = false; this.error = "";
      this.destroyMap();
      this.res = null; this.criteria = null; this.query = ""; this.selected = [];
      this._demoCollectionKey = crypto.randomUUID(); this._demoCollectionId = null; this._demoSignature = null;
      this.view = "search";
    },

    // --- чипы и фильтры ---
    removeProduct() { this.criteria.product = null; this.scheduleSearch(); },
    removeCategory() { this.criteria.category = null; this.criteria.product = null; this.scheduleSearch(); },
    removeVolume() { this.criteria.volume_kg_month = null; this.filters.fit_min_order = false; this.scheduleSearch(); },
    removePrice() { this.criteria.max_price_rub = null; this.scheduleSearch(); },
    removeCond(list, key) {
      this.criteria[list] = this.criteria[list].filter((k) => k !== key);
      if (key === "deferral" && !this.criteria.required.concat(this.criteria.preferred).includes("deferral")) this.criteria.deferral_days = null;
      this.scheduleSearch();
    },
    acceptSuggestion(key) {
      this.criteria.suggestions = this.criteria.suggestions.filter((k) => k !== key);
      if (!this.criteria.preferred.includes(key)) this.criteria.preferred.push(key);
      this.scheduleSearch();
    },
    isRequired(key) { return this.criteria?.required.includes(key); },
    toggleRequired(key) {
      const c = this.criteria;
      if (c.required.includes(key)) c.required = c.required.filter((k) => k !== key);
      else { c.required.push(key); c.preferred = c.preferred.filter((k) => k !== key); c.suggestions = c.suggestions.filter((k) => k !== key); }
      this.scheduleSearch();
    },
    relaxCond(key) {
      const c = this.criteria;
      c.required = c.required.filter((k) => k !== key);
      if (!c.preferred.includes(key)) c.preferred.push(key);
      this.search();
    },
    showCritical() { this.filters.hide_critical = false; this.search(); },
    condChipText(key) {
      if (key === "deferral" && this.criteria.deferral_days) return `отсрочка ${this.criteria.deferral_days} дн.`;
      return this.condLabel(key).toLowerCase();
    },
    get radiusValue() { return this.filters.max_distance_km ?? this.criteria?.radius_km ?? 300; },
    setRadius(v) { this.filters.max_distance_km = Number(v); this.scheduleSearch(); },
    get hiddenTotal() { return (this.res?.hidden || []).reduce((s, h) => s + h.count, 0); },
    get criticalHidden() { return (this.res?.hidden || []).find((h) => h.reason.startsWith("Критичные"))?.count || 0; },

    // --- карточка результата ---
    pros(r) {
      return r.reasons.concat(r.flags.filter((f) => f.level === "ok").map((f) => f.text)).slice(0, 4);
    },
    prosHint(r, line) { return r.flags.find((f) => f.text === line)?.hint || ""; },
    cons(r) { return r.flags.filter((f) => f.level !== "ok").sort((a, b) => (a.level === "bad" ? -1 : 0) - (b.level === "bad" ? -1 : 0)); },
    priceText(offer, currency) {
      if (!offer) return "";
      if (offer.price == null) return "цена по запросу";
      if (currency === "KZT") return `${money(offer.price)} ₸/${offer.unit} ≈ ${money(offer.price_rub)} ₽`;
      return `${money(offer.price)} ₽/${offer.unit}`;
    },
    scoreClass,
    money,
    fmtDate,
    fmtDateTime,
    isSelected(id) { return this.selected.some((s) => s.id === id); },
    toggleSelect(r) {
      if (this.isSelected(r.supplier.id)) this.selected = this.selected.filter((s) => s.id !== r.supplier.id);
      else if (this.selected.length < 4) this.selected.push({ id: r.supplier.id, name: r.supplier.name, preferred_channel: r.supplier.preferred_channel });
      else { this.toast("Можно выбрать до 4 поставщиков"); return; }
      this.persistSelection("demo", this.selected.map(s => String(s.id))).catch(e => this.toast(e.message));
    },
    get noContactSelected() { return this.selected.filter((s) => !s.preferred_channel); },

    // --- карта ---
    destroyMap() {
      if (map) map.remove();
      map = null; markersLayer = null; markersById = {};
    },
    refreshMap() {
      if (this.view !== "search" || !this.res || this.layout === "list" || !window.L) return;
      const el = document.getElementById("map");
      if (!el) return;
      if (map && map.getContainer() !== el) this.destroyMap();
      if (!map) {
        map = L.map(el, { zoomControl: true, attributionControl: true });
        const tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 18, attribution: "© OpenStreetMap",
        }).addTo(map);
        tiles.on("tileerror", () => { this.mapOffline = true; });
        tiles.on("tileload", () => { this.mapOffline = false; });
        markersLayer = L.layerGroup().addTo(map);
      }
      map.invalidateSize();
      markersLayer.clearLayers();
      markersById = {};
      const o = this.res.origin;
      if (this.criteria?.location_unresolved) { map.setView([o.lat, o.lon], 5); return; }
      const pts = [[o.lat, o.lon]];
      const originLabel = document.createElement("span");
      originLabel.textContent = `Точка доставки: ${o.label}`;
      L.marker([o.lat, o.lon], {
        icon: L.divIcon({ className: "", html: '<div class="origin-pin" title="Точка доставки">★</div>', iconSize: [26, 26], iconAnchor: [13, 13] }),
        zIndexOffset: 1000,
      }).bindTooltip(originLabel).addTo(markersLayer);
      L.circle([o.lat, o.lon], {
        radius: this.radiusValue * 1000, color: "#21ffff", weight: 1, opacity: 0.5, fillOpacity: 0.04, dashArray: "4 6",
      }).addTo(markersLayer);
      this.res.results.forEach((r, i) => {
        const s = r.supplier;
        const icon = L.divIcon({ className: "", html: `<div class="pin ${scoreClass(r.score)}">${i + 1}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
        const m = L.marker([s.lat, s.lon], { icon }).addTo(markersLayer);
        const popup = document.createElement("div");
        const b = document.createElement("b"); b.textContent = `${i + 1}. ${s.name}`;
        const line1 = document.createElement("div"); line1.textContent = `Балл ${r.score} · ${this.priceText(r.offer, s.currency)}`;
        const line2 = document.createElement("div"); line2.className = "mut"; line2.textContent = r.logistics_text;
        popup.append(b, line1, line2);
        m.bindPopup(popup);
        m.on("click", () => { this.highlight(s.id, true); });
        markersById[s.id] = m;
        pts.push([s.lat, s.lon]);
      });
      if (pts.length > 1) map.fitBounds(pts, { padding: [40, 40], maxZoom: 11 });
      else map.setView(pts[0], 10);
    },
    highlight(id, scroll = false) {
      this.hlId = id;
      Object.entries(markersById).forEach(([sid, m]) => m.getElement()?.firstElementChild?.classList.toggle("hl", Number(sid) === id));
      if (scroll) document.getElementById(`card-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    },

    // --- карточка поставщика ---
    async openDetail(id, scored = null) {
      try {
        const d = await api(`/suppliers/${id}`);
        this.detail = { ...d, scored: scored || this.res?.results.find((r) => r.supplier.id === id) || null };
        this.factTab = "all";
        this.noteText = "";
        this.$nextTick(() => this.$refs.detailDlg.showModal());
      } catch (e) { this.toast(e.message); }
    },
    closeDetail() { this.$refs.detailDlg.close(); },
    facts(group) {
      return this.factTab === "all" ? group.facts : group.facts.filter((f) => f.level === this.factTab);
    },
    docStatus(d) {
      if (!d.valid_until) return { cls: "ok", text: d.kind === "declaration" || d.kind === "certificate" ? "срок не указан" : "зарегистрирован" };
      const days = Math.round((new Date(d.valid_until) - new Date(new Date().toDateString())) / 86400000);
      const mmYYYY = new Date(d.valid_until).toLocaleDateString("ru-RU", { month: "2-digit", year: "numeric" });
      if (days < 0) return { cls: "bad", text: `истекла ${mmYYYY}` };
      if (days <= 30) return { cls: "warn", text: `истекает через ${days} дн.` };
      return { cls: "ok", text: `действует до ${mmYYYY}` };
    },
    docLabel(kind) { return DOC_LABELS[kind] || kind; },
    srcText(key) { return this.detail?.supplier.sources[key] ? `источник: ${this.detail.supplier.sources[key]}` : ""; },
    partWeight(part) { return this.meta.weights[this.mode]?.[part] ?? 0; },
    partLabel(part) { return PART_LABELS[part]; },
    async addNote() {
      if (!this.noteText.trim()) return;
      try {
        const n = await api(`/suppliers/${this.detail.supplier.id}/notes`, { method: "POST", body: { author: this.profile.contact_name || "Я", text: this.noteText } });
        this.detail.notes.unshift(n);
        this.noteText = "";
      } catch (e) { this.toast(e.message); }
    },
    detailSelect() {
      const s = this.detail.supplier;
      if (!this.isSelected(s.id)) this.toggleSelect({ supplier: s });
    },

    // --- сравнение ---
    async openCompare(ids = null, criteria = null) {
      try {
        this.compare = await api("/compare", {
          method: "POST",
          body: { criteria: criteria || this.criteria, supplier_ids: ids || this.selected.map((s) => s.id), mode: this.mode },
        });
        this.$nextTick(() => this.$refs.compareDlg.showModal());
      } catch (e) { this.toast(e.message); }
    },
    cmpRows() {
      const it = this.compare.items;
      return [
        { key: "score", label: "Балл", vals: it.map((x) => String(x.score)) },
        { key: "price", label: "Цена", vals: it.map((x) => this.priceText(x.offer, x.supplier.currency) || "—") },
        { key: "min_order", label: "Мин. партия", vals: it.map((x) => `${x.supplier.min_order_kg} кг`) },
        { key: "distance", label: "Расстояние", vals: it.map((x) => `${Math.round(x.distance_km)} км`) },
        { key: "delivery_days", label: "Срок доставки", vals: it.map((x) => `${x.supplier.delivery_days} дн.`) },
        { key: "deferral", label: "Отсрочка", vals: it.map((x) => (x.supplier.deferral_days == null ? "нет данных" : x.supplier.deferral_days ? `${x.supplier.deferral_days} дн.` : "нет")) },
        { key: "reliability", label: "Надёжность", vals: it.map((x) => `${x.parts.reliability} / 100`) },
        { key: "docs", label: "Документы", vals: it.map((x) => x.supplier.documents.map((d) => DOC_LABELS[d.kind]).join(", ") || "нет данных") },
        { key: "risks", label: "Риски", vals: it.map((x) => x.flags.filter((f) => f.level === "bad").map((f) => "⛔ " + f.text).join("; ") || "критичных нет") },
      ];
    },
    isBest(key, i) { return this.compare.best[key] === this.compare.items[i].supplier.id; },

    // --- запрос КП ---
    async openKP(suppliers = null) {
      const list = suppliers || this.selected;
      const criteria = this.compare?.criteria || this.criteria || {};
      if (!list.length) return;
      try {
        await this.persistSelection("demo", list.map(s => String(s.id)), criteria);
        const draft = await api("/kp/draft", { method: "POST", body: { criteria, supplier_ids: list.map((s) => s.id) } });
        const full = await Promise.all(list.map((s) => api(`/suppliers/${s.id}`).then((d) => d.supplier)));
        this.kp = {
          ...draft, criteria, collectionId: this._demoCollectionId,
          label: criteria.product ? (this.meta.products?.find(p => p.key === criteria.product)?.name || criteria.product) : criteria.category ? this.catName(criteria.category) : "Запрос КП",
          rows: full.map((s) => ({ supplier: s, channel: s.preferred_channel || "copy", sent: null, busy: false })),
        };
        this.compare && this.$refs.compareDlg.open && this.$refs.compareDlg.close();
        this.$refs.detailDlg?.open && this.$refs.detailDlg.close();
        this.$nextTick(() => this.$refs.kpDlg.showModal());
      } catch (e) { this.toast(e.message); }
    },
    channelsFor(s) {
      return ["whatsapp", "email", "telegram", "copy"].filter((c) => c === "copy" || s[c]);
    },
    channelLabel(c) { return CHANNELS[c]; },
    channelDone(c) { return c === "copy" ? "текст скопирован" : CHANNELS[c]; },
    contactFor(row) {
      const s = row.supplier;
      return { whatsapp: s.whatsapp, email: s.email, telegram: s.telegram, copy: s.phone ? `тел. ${s.phone}` : "контактов нет" }[row.channel] || "";
    },
    linkFor(row) {
      const s = row.supplier, text = `${this.kp.subject}\n\n${this.kp.body}`;
      if (row.channel === "email") return `mailto:${s.email}?subject=${encodeURIComponent(this.kp.subject)}&body=${encodeURIComponent(this.kp.body)}`;
      if (row.channel === "whatsapp") return `https://wa.me/${(s.whatsapp || "").replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
      if (row.channel === "telegram") return `https://t.me/share/url?url=${encodeURIComponent(" ")}&text=${encodeURIComponent(text)}`;
      return null;
    },
    async sendRow(row) {
      const link = this.linkFor(row);
      if (row.busy || row.sent) return;
      if (link) window.open(link, "_blank", "noopener");
      else if (!await this.copyText()) return;
      if (row.busy || row.sent) return;
      row.busy = true;
      try {
        row.sent = { prepared: true };
      } catch (e) { this.toast(e.message); } finally { row.busy = false; }
    },
    async copyText() {
      try {
        await navigator.clipboard.writeText(`${this.kp.subject}\n\n${this.kp.body}`);
        this.toast("Текст запроса скопирован");
        return true;
      } catch { this.toast("Не удалось скопировать — выделите текст вручную"); return false; }
    },
    get kpSentCount() { return this.kp ? this.kp.rows.filter((r) => r.sent).length : 0; },
    finishKP() {
      this.$refs.kpDlg.close();
      this.toast("Компании сохранены в «Моих подборках». Отметьте отправку после отправки письма.");
    },

    // --- Мои подборки ---
    async showCurrentCollection() {
      await collectionSaveQueue;
      if (this.saveError) { this.toast(this.saveError); return; }
      this.view = "collections";
      const c = this.collections.find(c => c.id === this._demoCollectionId);
      if (c) { this.openCollectionId = null; this.toggleCollection(c); }
    },
    async loadCollections() {
      this.collectionsLoading = true; this.collectionsError = "";
      try { this.collections = await api("/collections"); }
      catch (e) { this.collectionsError = e.message; throw e; }
      finally { this.collectionsLoading = false; }
    },
    persistSelection(source, ids, criteria = null) {
      const search = source === "demo" ? JSON.parse(JSON.stringify({ criteria: criteria || this.criteria || {}, filters: this.filters, mode: this.mode, sort: this.sort })) : null;
      if (source === "demo") {
        const signature = JSON.stringify(search);
        if (this._demoSignature && this._demoSignature !== signature) { this._demoCollectionKey = crypto.randomUUID(); this._demoCollectionId = null; }
        this._demoSignature = signature;
      }
      const key = source === "demo" ? this._demoCollectionKey : this._realCollectionKey;
      if (!ids.length && !queuedCollectionKeys.has(key)) return Promise.resolve(null);
      queuedCollectionKeys.add(key);
      const body = { search_key: key, source, search, supplier_ids: [...ids] };
      this.pendingSaves++;
      const operation = collectionSaveQueue.then(async () => {
        try {
          const saved = await api("/collections", { method: "POST", body });
          if (source === "demo" && key === this._demoCollectionKey) this._demoCollectionId = saved.id;
          if (source === "real" && key === this._realCollectionKey) this._realCollectionId = saved.id;
          this.saveError = "";
          this.replaceCollection(saved);
          return saved;
        } catch (e) { this.saveError = "Подборка не сохранена: " + e.message; throw e; }
        finally { this.pendingSaves--; }
      });
      collectionSaveQueue = operation.catch(() => {});
      return operation;
    },
    replaceCollection(saved) {
      const index = this.collections.findIndex(c => c.id === saved.id);
      if (index < 0) this.collections.unshift(saved); else this.collections.splice(index, 1, saved);
    },
    toggleCollection(c) {
      if (this.openCollectionId === c.id) { this.openCollectionId = null; return; }
      this.openCollectionId = c.id;
      if (!this.collectionChecked[c.id]) this.collectionChecked[c.id] = c.items.slice(0, 4).map(i => i.id);
    },
    collectionLabels(c) {
      const q = c.search.criteria;
      const labels = [c.city];
      if (q.volume_kg_month) labels.push(`${money(q.volume_kg_month)} кг/мес`);
      if (q.max_price_rub != null) labels.push(`до ${money(q.max_price_rub)} ₽`);
      for (const key of q.required) labels.push(`обязательно: ${this.condLabel(key)}`);
      for (const key of q.preferred) labels.push(`желательно: ${this.condLabel(key)}${key === "deferral" && q.deferral_days ? ` ${q.deferral_days} дн.` : ""}`);
      return labels;
    },
    quoteMoney(value) { return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 }); },
    memberFact(item, key) {
      const fact = item.supplier.facts.find(f => f.key === key);
      return fact?.value || fact?.missing_reason || "Уточнить у поставщика";
    },
    checkedMembers(c) { return c.items.filter(i => (this.collectionChecked[c.id] || []).includes(i.id)); },
    offerMembers(c) { return this.checkedMembers(c).filter(i => i.status === "received" && i.price_rub != null); },
    toggleMember(c, item, event) {
      const ids = this.collectionChecked[c.id] || [];
      if (ids.includes(item.id)) this.collectionChecked[c.id] = ids.filter(id => id !== item.id);
      else if (ids.length < 4) this.collectionChecked[c.id] = [...ids, item.id];
      else this.toast("Выберите до 4 компаний для сравнения");
      event.target.checked = this.collectionChecked[c.id].includes(item.id);
    },
    memberStatus(status) { return { selected: "Выбран", sent: "Запрос отправлен", received: "Ответ получен" }[status]; },
    async updateMember(c, item, fields) {
      const saved = await api(`/collections/${c.id}/items/${item.id}`, { method: "PATCH", body: fields });
      this.replaceCollection(saved);
      return saved;
    },
    async markSent(c, item) {
      try { await this.updateMember(c, item, { status: "sent" }); this.toast("Отправка отмечена"); }
      catch (e) { this.toast(e.message); }
    },
    editReply(c, item) {
      this.replyEditor = { collectionId: c.id, itemId: item.id, price: item.price_rub ?? "", terms: item.terms, note: item.note, busy: false };
    },
    async saveReply(c, item) {
      const editor = this.replyEditor;
      if (!editor || editor.busy) return;
      editor.busy = true;
      try {
        await this.updateMember(c, item, { status: "received", price_rub: editor.price === "" ? null : Number(editor.price), terms: editor.terms, note: editor.note });
        this.replyEditor.itemId = null;
        this.toast("Ответ сохранён в подборке");
      } catch (e) { editor.busy = false; this.toast(e.message); }
    },
    async compareCollection(c, kind) {
      const items = kind === "offers" ? this.offerMembers(c) : this.checkedMembers(c);
      try {
        this.collectionComparison = await api(`/collections/${c.id}/compare`, { method: "POST", body: { kind, item_ids: items.map(i => i.id) } });
        this.$nextTick(() => this.$refs.collectionCompareDlg.showModal());
      } catch (e) { this.toast(e.message); }
    },
    get collectionComparisonRows() {
      const cmp = this.collectionComparison;
      if (!cmp) return [];
      if (cmp.kind === "offers") return [
        { label: `Цена из ответа, ₽/${cmp.unit}`, values: cmp.items.map(i => this.quoteMoney(i.price_rub)), price: true },
        { label: "Условия из ответа", values: cmp.items.map(i => i.terms || "Не внесены — уточните") },
        { label: "Заметка", values: cmp.items.map(i => i.note || "—") },
      ];
      const keys = [...new Set(cmp.items.flatMap(i => i.supplier.facts.map(f => f.key)))];
      return keys.map(key => ({ label: cmp.items.flatMap(i => i.supplier.facts).find(f => f.key === key).label, values: cmp.items.map(i => this.memberFact(i, key)) }));
    },
    async draftForMember(c, item) {
      try {
        const draft = await api(`/collections/${c.id}/items/${item.id}/draft`, { method: "POST" });
        this.collectionDraft = { ...draft, collectionId: c.id, itemId: item.id, supplier: item.supplier, status: item.status };
        this.$nextTick(() => this.$refs.collectionDraftDlg.showModal());
      } catch (e) { this.toast(e.message); }
    },
    draftContactLink(channel) {
      const d = this.collectionDraft;
      if (!d) return "";
      const text = `${d.subject}\n\n${d.body}`;
      if (channel === "email" && d.supplier.email) return `mailto:${d.supplier.email}?subject=${encodeURIComponent(d.subject)}&body=${encodeURIComponent(d.body)}`;
      if (channel === "whatsapp" && d.supplier.whatsapp) return `https://wa.me/${d.supplier.whatsapp.replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
      return "";
    },
    async copyCollectionDraft() {
      try { await navigator.clipboard.writeText(`${this.collectionDraft.subject}\n\n${this.collectionDraft.body}`); this.toast("Текст скопирован. Отметьте отправку, когда отправите письмо."); }
      catch { this.toast("Выделите текст вручную: копирование недоступно"); }
    },
    async confirmDraftSent() {
      const d = this.collectionDraft;
      try {
        const saved = await api(`/collections/${d.collectionId}/items/${d.itemId}`, { method: "PATCH", body: { status: "sent" } });
        this.replaceCollection(saved); d.status = "sent";
        this.toast("Отправка отмечена в подборке");
      } catch (e) { this.toast(e.message); }
    },
    async repeatCollection(c) {
      if (c.source === "real") { await this.openRealCase(); return; }
      this.criteria = JSON.parse(JSON.stringify(c.search.criteria));
      this.query = this.criteria.raw || c.title;
      this.filters = { ...c.search.filters }; this.mode = c.search.mode; this.sort = c.search.sort;
      this.selected = []; this._demoCollectionKey = crypto.randomUUID(); this._demoCollectionId = null; this._demoSignature = null;
      this.view = "search"; await this.search();
    },

    // --- профиль ---
    openProfile() { this.profileForm = { ...this.profile }; this.$nextTick(() => this.$refs.profileDlg.showModal()); },
    async saveProfile() {
      try {
        const { lat, lon, ...body } = this.profileForm;
        this.profile = await api("/profile", { method: "PUT", body: { ...body, points: Number(body.points) } });
        this.$refs.profileDlg.close();
        this.toast("Профиль сохранён");
        if (this.criteria) this.search();
      } catch (e) { this.toast(e.message); }
    },

    // --- демо ---
    async resetDemo() {
      await collectionSaveQueue;
      await api("/demo/reset", { method: "POST" });
      this.$refs.resetDlg.close();
      this.profile = await api("/profile");
      await this.loadCollections();
      this.selected = []; this._demoCollectionKey = crypto.randomUUID(); this._demoCollectionId = null; this._demoSignature = null;
      this._realCollectionKey = crypto.randomUUID(); this._realCollectionId = null;
      this.openCollectionId = null; this.collectionChecked = {}; this.replyEditor.itemId = null;
      if (this.criteria) await this.search();
      this.toast("Демо-данные восстановлены");
    },

    // --- тост (popover="manual") ---
    toast(text) {
      this.toastText = text;
      const el = this.$refs.toast;
      if (el.showPopover) { try { el.hidePopover(); } catch {} el.showPopover(); } else el.classList.add("show");
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { if (el.hidePopover) { try { el.hidePopover(); } catch {} } else el.classList.remove("show"); }, 3200);
    },
  }));
});
