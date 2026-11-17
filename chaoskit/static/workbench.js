const form = document.querySelector("#rule-form");
const fields = form.elements;
const title = document.querySelector("#form-title");
const message = document.querySelector("#form-message");
const valueLabel = document.querySelector("#value-label");
const faults = {
  delay: ["延迟时间（毫秒）", 5000, 1, 15000],
  status: ["HTTP 状态码", 500, 400, 599],
  abort: ["故障参数", 0, 0, 0],
  timeout: ["最长等待（毫秒）", 3200, 1, 15000],
};

function fieldMessage(field) {
  if (field === "id") return "规则编号须为 1–48 个英文字母、数字、下划线或连字符。";
  if (field === "path") return "路径须以 / 开头，可用英文字母、数字和 -_.~/*。** 必须独占一段。";
  if (field === "priority") return "请输入 0–9999 之间的整数作为优先级。";
  if (field === "value") {
    const [name, , min, max] = faults[fields.fault.value];
    return `请输入 ${min}–${max} 之间的整数作为${name}。`;
  }
  return "规则未保存，请检查路径匹配表达式和故障参数。";
}

function clearError() {
  message.textContent = "";
  form.querySelector("[aria-invalid]")?.removeAttribute("aria-invalid");
}

function showFieldError(field) {
  message.textContent = fieldMessage(field);
  const control = fields.namedItem(field);
  if (control) {
    control.setAttribute("aria-invalid", "true");
    control.focus();
  }
}

function updateValue(reset) {
  const [name, defaultValue, min, max] = faults[fields.fault.value];
  valueLabel.querySelector("span").textContent = name;
  valueLabel.hidden = fields.fault.value === "abort";
  fields.value.min = min;
  fields.value.max = max;
  if (reset) fields.value.value = defaultValue;
}

async function write(id, method, rule) {
  clearError();
  try {
    const response = await fetch("/_chaoskit/rules/" + encodeURIComponent(id), {
      method, headers: {"Content-Type": "application/json"},
      body: rule ? JSON.stringify(rule) : undefined,
    });
    if (!response.ok) {
      if (response.status === 422) {
        const {detail} = await response.json();
        showFieldError(detail?.[0]?.loc?.[1] ?? "path");
        return;
      }
      message.textContent = `规则操作失败（HTTP ${response.status}），请刷新控制台后重试。`;
      return;
    }
    location.reload();
  } catch {
    message.textContent = "无法连接故障注入控制台，请确认代理正在运行后重试。";
  }
}

form.addEventListener("input", clearError);
fields.fault.addEventListener("change", () => updateValue(true));
form.addEventListener("reset", () => requestAnimationFrame(() => {
  fields.id.readOnly = false;
  title.textContent = "新建规则";
  clearError();
  updateValue(true);
}));
form.addEventListener("submit", event => {
  event.preventDefault();
  const invalid = Array.from(fields).find(field => field.willValidate && !field.validity.valid);
  if (invalid) {
    showFieldError(invalid.name);
    return;
  }
  const rule = Object.fromEntries(new FormData(form));
  Object.assign(rule, {enabled: fields.enabled.checked, value: Number(rule.value), priority: Number(rule.priority)});
  write(rule.id, "PUT", rule);
});
document.querySelector(".rule-list").addEventListener("click", event => {
  const action = event.target.dataset.action;
  if (!action) return;
  const rule = JSON.parse(event.target.closest("[data-rule]").dataset.rule);
  if (action === "delete") write(rule.id, "DELETE");
  if (action === "toggle") write(rule.id, "PUT", {...rule, enabled: !rule.enabled});
  if (action === "edit") {
    for (const key of ["id", "path", "method", "fault", "value", "priority"]) fields[key].value = rule[key];
    fields.id.readOnly = true;
    fields.enabled.checked = rule.enabled;
    title.textContent = `编辑规则 ${rule.id}`;
    clearError();
    updateValue(false);
    fields.path.focus();
  }
});
updateValue(true);

document.querySelector(".new-rule").addEventListener("click", () => form.reset());

const search = document.querySelector("#record-search");
const query = document.querySelector("#record-query");
const clearSearch = document.querySelector("#search-clear");
const scopes = document.querySelector(".scope-filters");
const scopeButtons = Array.from(scopes.querySelectorAll("[data-scope]"));
const searchStatus = document.querySelector("#search-status");
const resultGroups = Array.from(document.querySelectorAll("[data-results]"), section => ({
  section,
  rows: Array.from(section.querySelectorAll("[data-row]"), row => ({
    row,
    text: Array.from(row.children, child => child.textContent.trim()).join(" ").toLowerCase(),
  })),
  count: section.querySelector("[data-count]"),
  empty: section.querySelector("[data-empty]"),
  noMatch: section.querySelector("[data-no-match]"),
  table: section.querySelector(".table-wrap"),
  hint: section.querySelector(".table-hint"),
}));

const url = new URL(location.href);
const initialScope = url.searchParams.get("scope");
let scope = scopeButtons.some(button => button.dataset.scope === initialScope) ? initialScope : "all";
query.value = url.searchParams.get("q") ?? "";

function filterRecords() {
  const phrase = query.value.trim();
  const terms = phrase.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const group of resultGroups) {
    group.section.hidden = scope !== "all" && scope !== group.section.dataset.results;
    let count = 0;
    for (const {row, text} of group.rows) {
      row.hidden = !terms.every(term => text.includes(term));
      if (!row.hidden) count++;
    }
    group.count.textContent = `${count} 条`;
    group.noMatch.hidden = count > 0 || !phrase;
    if (group.empty) group.empty.hidden = Boolean(phrase);
    if (group.table) group.table.hidden = count === 0;
    if (group.hint) group.hint.hidden = count === 0;
    if (!group.section.hidden) total += count;
  }
  clearSearch.hidden = query.value === "";
  for (const button of scopeButtons) button.setAttribute("aria-pressed", button.dataset.scope === scope);
  searchStatus.textContent = `找到 ${total} 条记录`;
  if (phrase) url.searchParams.set("q", phrase);
  else url.searchParams.delete("q");
  if (scope === "all") url.searchParams.delete("scope");
  else url.searchParams.set("scope", scope);
  history.replaceState(null, "", url);
  document.querySelector(".refresh-logs").href = url.pathname + url.search;
}

search.addEventListener("submit", event => {
  event.preventDefault();
  filterRecords();
});
query.addEventListener("input", filterRecords);
clearSearch.addEventListener("click", () => {
  query.value = "";
  filterRecords();
  query.focus();
});
for (const button of scopeButtons) button.addEventListener("click", () => {
  scope = button.dataset.scope;
  filterRecords();
});
search.hidden = false;
scopes.hidden = false;
filterRecords();
