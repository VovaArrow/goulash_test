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
const STATUS = { sent: "Отправлено", waiting: "Ждём ответ", received: "КП получено", declined: "Отказ" };
const PART_LABELS = { match: "Соответствие запросу", reliability: "Надёжность", price: "Цена", logistics: "Логистика" };

// Leaflet держим вне реактивного состояния Alpine.
let map = null, markersLayer = null, markersById = {};

document.addEventListener("alpine:init", () => {
  Alpine.data("app", () => ({
    view: "search",
    meta: { categories: [], cities: [], conditions: [], modes: [], examples: [], weights: {} },
    profile: null,
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
    requests: [],
    openEvents: {},
    profileForm: null,
    confirmReset: false,
    toastText: "",
    mapOffline: false,
    _searchTimer: null,

    async init() {
      if (window.matchMedia("(max-width: 900px)").matches) this.layout = "list";
      this.meta = await api("/meta");
      this.profile = await api("/profile");
      await this.loadRequests();
      this.$watch("layout", () => this.$nextTick(() => this.refreshMap()));
      this.$watch("view", (v) => { if (v === "requests") this.loadRequests(); if (v === "search") this.$nextTick(() => this.refreshMap()); });
    },

    // --- справочники ---
    cityName(key) { return this.meta.cities.find((c) => c.key === key)?.name || key || ""; },
    catName(key) { return this.meta.categories.find((c) => c.key === key)?.label || key; },
    condLabel(key) { return this.meta.conditions.find((c) => c.key === key)?.label || key; },
    productName() {
      const o = this.res?.results.find((r) => r.offer && r.offer.key === this.criteria?.product)?.offer;
      return o?.name || this.criteria?.product;
    },
    supWord(n) { return plural(n, "поставщик", "поставщика", "поставщиков"); },
    get waitingCount() { return this.requests.filter((r) => r.status === "sent" || r.status === "waiting").length; },

    // --- поиск ---
    async submit(text) {
      if (text != null) this.query = text;
      if (!this.query.trim()) return;
      this.error = "";
      try {
        this.criteria = await api("/parse", { method: "POST", body: { text: this.query } });
        this.filters.max_distance_km = null;
        await this.search();
      } catch (e) { this.error = e.message; }
    },
    scheduleSearch() {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this.search(), 250);
    },
    async search() {
      if (!this.criteria) return;
      this.loading = true;
      try {
        this.res = await api("/search", {
          method: "POST",
          body: { criteria: this.criteria, filters: this.filters, mode: this.mode, sort: this.sort },
        });
        this.error = "";
        this.$nextTick(() => this.refreshMap());
      } catch (e) { this.error = e.message; } finally { this.loading = false; }
    },
    reset() {
      this.res = null; this.criteria = null; this.query = ""; this.selected = [];
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
      else this.toast("Можно выбрать до 4 поставщиков");
    },
    get noContactSelected() { return this.selected.filter((s) => !s.preferred_channel); },

    // --- карта ---
    refreshMap() {
      if (this.view !== "search" || !this.res || this.layout === "list" || !window.L) return;
      const el = document.getElementById("map");
      if (!el) return;
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
      const pts = [[o.lat, o.lon]];
      L.marker([o.lat, o.lon], {
        icon: L.divIcon({ className: "", html: '<div class="origin-pin" title="Точка доставки">★</div>', iconSize: [26, 26], iconAnchor: [13, 13] }),
        zIndexOffset: 1000,
      }).bindTooltip(`Точка доставки: ${o.label}`).addTo(markersLayer);
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
      if (!list.length) return;
      try {
        const draft = await api("/kp/draft", { method: "POST", body: { criteria: this.criteria || {}, supplier_ids: list.map((s) => s.id) } });
        const full = await Promise.all(list.map((s) => api(`/suppliers/${s.id}`).then((d) => d.supplier)));
        this.kp = {
          ...draft,
          label: this.criteria?.product ? this.productName() : this.criteria?.category ? this.catName(this.criteria.category) : "Запрос КП",
          rows: full.map((s) => ({ supplier: s, channel: s.preferred_channel || "copy", sent: null })),
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
      if (link) window.open(link, "_blank", "noopener");
      else await this.copyText();
      try {
        const [created] = await api("/kp", { method: "POST", body: { product_label: this.kp.label, items: [{ supplier_id: row.supplier.id, channel: row.channel }] } });
        row.sent = created;
        await this.loadRequests();
      } catch (e) { this.toast(e.message); }
    },
    async copyText() {
      try {
        await navigator.clipboard.writeText(`${this.kp.subject}\n\n${this.kp.body}`);
        this.toast("Текст запроса скопирован");
      } catch { this.toast("Не удалось скопировать — выделите текст вручную"); }
    },
    get kpSentCount() { return this.kp ? this.kp.rows.filter((r) => r.sent).length : 0; },
    finishKP() {
      this.$refs.kpDlg.close();
      if (this.kpSentCount) {
        this.toast(`${this.kpSentCount} ${plural(this.kpSentCount, "запрос сохранён", "запроса сохранено", "запросов сохранено")} во вкладке «Запросы КП»`);
        this.selected = [];
      }
    },

    // --- вкладка «Запросы КП» ---
    async loadRequests() { this.requests = await api("/kp"); },
    async patchRequest(r, fields) {
      try {
        const upd = await api(`/kp/${r.id}`, { method: "PATCH", body: fields });
        Object.assign(r, upd);
      } catch (e) { this.toast(e.message); }
    },
    savePrice(r, value) {
      const v = value === "" ? null : Number(value);
      if (v !== r.price_rub) this.patchRequest(r, { price_rub: v });
    },
    saveNote(r, value) { if (value !== (r.note || "")) this.patchRequest(r, { note: value }); },
    stepState(r, step) {
      const order = ["sent", "waiting", "received"];
      if (r.status === "declined") return step === "received" ? "declined" : "done";
      const cur = order.indexOf(r.status), idx = order.indexOf(step);
      return idx < cur ? "done" : idx === cur ? "cur done" : "";
    },
    statusLabel(s) { return STATUS[s]; },
    isOverdue(r) { return (r.status === "sent" || r.status === "waiting") && new Date(r.reply_due) < new Date(new Date().toDateString()); },
    get receivedIds() { return [...new Set(this.requests.filter((r) => r.status === "received").map((r) => r.supplier_id))].slice(0, 4); },
    async compareReceived() {
      const label = this.requests.find((r) => r.status === "received")?.product_label || "";
      const criteria = await api("/parse", { method: "POST", body: { text: label } });
      this.openCompare(this.receivedIds, criteria);
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
      await api("/demo/reset", { method: "POST" });
      this.$refs.resetDlg.close();
      this.profile = await api("/profile");
      await this.loadRequests();
      this.selected = [];
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
