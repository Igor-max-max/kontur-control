// Public preview: all demo data stays in the current browser; no network API is used.
const storageKey = "kontur-browser-demo-v1";
const descriptions = {
  reminders: "Считает остаток времени, определяет уровень риска и формирует локальный протокол напоминаний.",
  intake: "Распознаёт реквизиты письма и предлагает черновик карточки СЭД для проверки.",
  territories: "Преобразует снимки бумажного журнала в структурированную сводку для центра."
};
const modules = [
  { id: "reminders", title: "Напоминания и оценка риска", enabled: true },
  { id: "intake", title: "Приём документов вне СЭД", enabled: false },
  { id: "territories", title: "Свод документооборота терорганов", enabled: false }
];
const metricTitles = [
  "Время до регистрации документа",
  "Охват документооборота тероргана",
  "Документы с заблаговременным напоминанием",
  "Личные визиты для доставки документов"
];

function dateOffset(days) {
  const day = new Date();
  day.setHours(12, 0, 0, 0);
  day.setDate(day.getDate() + days);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
}

function initialData() {
  return {
    assignments: [
      { id: 1, doc_number: "ДЕМО-01", assignee: "Иванов И. И.", department: "Учебный отдел", due_date: dateOffset(-2), status: "В работе" },
      { id: 2, doc_number: "ДЕМО-02", assignee: "Петрова П. П.", department: "Отдел планирования", due_date: dateOffset(1), status: "В работе" },
      { id: 3, doc_number: "ДЕМО-03", assignee: "Сидоров С. С.", department: "Учебный отдел", due_date: dateOffset(6), status: "В работе" }
    ],
    reminders: [],
    metrics: metricTitles.map((title, id) => ({ id, title, baseline: null, current: null }))
  };
}

function loadData() {
  const raw = localStorage.getItem(storageKey);
  if (!raw) return initialData();
  const data = JSON.parse(raw);
  if (!Array.isArray(data.assignments) || !Array.isArray(data.reminders) || !Array.isArray(data.metrics)) {
    throw new Error("Неверный формат локальных данных");
  }
  return data;
}

let state;
try {
  state = loadData();
} catch (error) {
  state = initialData();
  document.querySelector("#notice").hidden = false;
  document.querySelector("#notice").textContent = "Не удалось прочитать локальные данные. Показаны учебные записи.";
}

function save() {
  localStorage.setItem(storageKey, JSON.stringify(state));
}

function riskFor(item) {
  if (item.status === "Исполнено") return { code: "done", label: "Исполнено" };
  const days = Math.round((new Date(`${item.due_date}T12:00:00`) - new Date(`${dateOffset(0)}T12:00:00`)) / 86400000);
  if (days < 0) return { code: "overdue", label: "Срок нарушен" };
  if (days <= 1) return { code: "critical", label: "Критический" };
  if (days <= 3) return { code: "high", label: "Высокий" };
  if (days <= 7) return { code: "medium", label: "Средний" };
  return { code: "low", label: "Низкий" };
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function showNotice(message, error = false) {
  const notice = document.querySelector("#notice");
  notice.textContent = message;
  notice.className = `notice${error ? " error" : ""}`;
  notice.hidden = false;
}

function render() {
  const active = state.assignments.filter(item => item.status !== "Исполнено");
  const risks = active.map(riskFor);
  const cards = [
    ["Поручений в работе", active.length, ""],
    ["Срок нарушен", risks.filter(risk => risk.code === "overdue").length, "alert"],
    ["Критический риск", risks.filter(risk => risk.code === "critical").length, "alert"],
    ["Напоминаний в протоколе", state.reminders.length, ""]
  ];
  document.querySelector("#summary").innerHTML = cards.map(([label, value, style]) =>
    `<article class="summary-card ${style}"><span>${label}</span><strong>${value}</strong></article>`
  ).join("");
  const body = document.querySelector("#assignment-list");
  body.innerHTML = state.assignments.map(item => {
    const risk = riskFor(item);
    const date = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${item.due_date}T12:00:00`));
    return `<tr>
      <td><span class="doc-number">${escapeHtml(item.doc_number)}</span><span class="tag">демо</span></td>
      <td>${escapeHtml(item.assignee)}</td>
      <td>${escapeHtml(item.department)}</td>
      <td>${date}</td>
      <td><span class="risk ${risk.code}">${risk.label}</span></td>
      <td>${item.status === "Исполнено" ? "Исполнено" : `<button class="button secondary small" data-complete="${item.id}">Исполнено</button>`}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="6">Поручений пока нет.</td></tr>';
  document.querySelector("#module-list").innerHTML = modules.map((module, index) => `
    <article class="module-card">
      <span class="status ${module.enabled ? "" : "locked"}">${module.enabled ? "Доступно в демо" : "Недоступно в демо"}</span>
      <h3>${module.title}</h3><p>${descriptions[module.id]}</p><span class="number">0${index + 1}</span>
    </article>`).join("");
  document.querySelector("#metric-list").innerHTML = state.metrics.map(metric => `
    <div class="metric-row" data-metric="${metric.id}">
      <strong>${escapeHtml(metric.title)}</strong>
      <input type="number" step="any" name="baseline" aria-label="Базовое значение" placeholder="База" value="${escapeHtml(metric.baseline ?? "")}">
      <input type="number" step="any" name="current" aria-label="Повторный замер" placeholder="После" value="${escapeHtml(metric.current ?? "")}">
      <button class="button secondary small" data-save-metric="${metric.id}">Сохранить</button>
    </div>`).join("");
}

document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab, .panel").forEach(element => element.classList.remove("active"));
  tab.classList.add("active");
  document.querySelector(`#${tab.dataset.panel}-panel`).classList.add("active");
}));

document.querySelector("#run-button").addEventListener("click", () => {
  const today = dateOffset(0);
  let created = 0;
  for (const item of state.assignments) {
    const risk = riskFor(item);
    if (["done", "low"].includes(risk.code) || state.reminders.some(row => row.id === item.id && row.date === today)) continue;
    state.reminders.push({ id: item.id, date: today });
    created++;
  }
  try {
    save();
    render();
    showNotice(created ? `Создано локальных записей напоминаний: ${created}. Письма не отправляются.` : "Новых напоминаний нет: сегодняшние уже сформированы или сроки ещё не приближаются.");
  } catch (error) { showNotice(`Не удалось сохранить: ${error.message}`, true); }
});

document.querySelector("#assignment-list").addEventListener("click", event => {
  const id = Number(event.target.dataset.complete);
  if (!id) return;
  const item = state.assignments.find(row => row.id === id);
  if (!item) return;
  item.status = "Исполнено";
  try { save(); render(); } catch (error) { showNotice(`Не удалось сохранить: ${error.message}`, true); }
});

const dialog = document.querySelector("#assignment-dialog");
document.querySelector("#add-button").addEventListener("click", () => dialog.showModal());
document.querySelector(".close").addEventListener("click", () => dialog.close());
document.querySelector("#assignment-form").addEventListener("submit", event => {
  event.preventDefault();
  const form = Object.fromEntries(new FormData(event.target));
  if (state.assignments.some(item => item.doc_number === form.doc_number.trim())) {
    showNotice("Поручение с таким номером уже есть в этом браузере.", true);
    return;
  }
  const id = Math.max(0, ...state.assignments.map(item => item.id)) + 1;
  state.assignments.push({ id, doc_number: form.doc_number.trim(), assignee: form.assignee.trim(), department: form.department.trim(), due_date: form.due_date, status: "В работе" });
  try {
    save();
    event.target.reset();
    dialog.close();
    showNotice("Учебное поручение сохранено в этом браузере.");
    render();
  } catch (error) { showNotice(`Не удалось сохранить: ${error.message}`, true); }
});

document.querySelector("#metric-list").addEventListener("click", event => {
  const id = event.target.dataset.saveMetric;
  if (id === undefined) return;
  const row = event.target.closest(".metric-row");
  const metric = state.metrics.find(item => item.id === Number(id));
  if (!metric) return;
  metric.baseline = row.querySelector('[name="baseline"]').value || null;
  metric.current = row.querySelector('[name="current"]').value || null;
  try {
    save();
    event.target.textContent = "Сохранено";
    setTimeout(() => { event.target.textContent = "Сохранить"; }, 1200);
  } catch (error) { showNotice(`Не удалось сохранить: ${error.message}`, true); }
});

render();
