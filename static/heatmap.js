"use strict";

const calendar = document.querySelector("[data-publication-calendar]");
const dates = calendar.querySelector(".calendar-data").content.querySelectorAll("time");
const select = calendar.querySelector("select");
const months = calendar.querySelector(".calendar-months");
const days = calendar.querySelector(".calendar-days");
const summary = calendar.querySelector(".calendar-summary");
const period = calendar.querySelector(".calendar-period");
const range = calendar.querySelector(".calendar-range");
const previousPeriod = calendar.querySelector(".calendar-prev");
const nextPeriod = calendar.querySelector(".calendar-next");
const detail = calendar.querySelector(".calendar-detail");
const detailTitle = detail.querySelector("h3");
const detailList = detail.querySelector("ul");
const narrow = matchMedia("(max-width: 580px)");
const publications = new Map();
for (const date of dates) {
  if (!publications.has(date.dateTime)) publications.set(date.dateTime, []);
  publications.get(date.dateTime).push(date.querySelector("a"));
}

const shanghai = new Intl.DateTimeFormat("en", {timeZone: "Asia/Shanghai", year: "numeric", month: "numeric"});
const current = Object.fromEntries(shanghai.formatToParts().map(part => [part.type, Number(part.value)]));
const currentQuarter = Math.floor((current.month - 1) / 3);
const years = new Set([current.year, ...Array.from(publications.keys(), date => Number(date.slice(0, 4)))]);
for (const year of [...years].sort((a, b) => b - a)) select.add(new Option(String(year), String(year)));
select.value = String(current.year);
let quarter = currentQuarter;

function showDay(cell) {
  const last = days.querySelector('[aria-pressed="true"]');
  if (last && last !== cell) last.setAttribute("aria-pressed", "false");
  cell.setAttribute("aria-pressed", "true");
  const entries = publications.get(cell.dataset.date) || [];
  detailTitle.textContent = cell.getAttribute("aria-label");
  detailList.replaceChildren();
  for (const article of entries) {
    detailList.appendChild(document.createElement("li")).append(article.cloneNode(true));
  }
  detailList.hidden = !entries.length;
  detail.hidden = false;
}

function render() {
  const year = Number(select.value);
  const compact = narrow.matches;
  calendar.toggleAttribute("data-quarter-view", compact);
  const start = compact ? quarter * 3 : 0;
  const end = compact ? start + 3 : 12;
  period.hidden = !compact;
  range.textContent = year + " 年 · " + (start + 1) + "–" + end + " 月";
  previousPeriod.disabled = quarter === 0;
  nextPeriod.disabled = quarter === 3;
  detail.hidden = true;
  const published = [...publications.entries()].filter(([date]) => date.startsWith(year + "-"));
  const total = published.reduce((sum, [, entries]) => sum + entries.length, 0);
  summary.textContent = year + " 年发布 " + total + " 篇 · " + published.length + " 个发文日";

  months.replaceChildren();
  days.replaceChildren();
  const day = new Date(Date.UTC(year, start, 1));
  const stop = new Date(Date.UTC(year, end, 1));
  const offset = (day.getUTCDay() + 6) % 7;
  const length = (stop - day) / 86_400_000;
  calendar.style.setProperty("--calendar-weeks", Math.ceil((length + offset) / 7));
  const cells = [];
  for (let i = 0; day < stop; i++, day.setUTCDate(day.getUTCDate() + 1)) {
    const date = day.toISOString().slice(0, 10);
    const index = i + offset;
    const week = Math.floor(index / 7) + 1;
    if (day.getUTCDate() === 1) {
      const label = document.createElement("span");
      label.style.gridColumn = week;
      label.textContent = day.getUTCMonth() + 1 + "月";
      months.append(label);
    }
    const count = publications.get(date)?.length || 0;
    const cell = document.createElement("button");
    cell.type = "button";
    cell.tabIndex = -1;
    cell.style.gridColumn = week;
    cell.style.gridRow = index % 7 + 1;
    cell.dataset.level = Math.min(count, 4);
    cell.dataset.date = date;
    cell.title = date + " · " + count + " 篇";
    cell.setAttribute("aria-label", cell.title);
    cell.setAttribute("aria-pressed", "false");
    days.append(cell);
    cells.push(cell);
  }
  (cells.find(cell => cell.dataset.level !== "0") || cells[0]).tabIndex = 0;
  days.onfocusin = event => {
    if (!cells.includes(event.target)) return;
    const previous = cells.find(cell => cell.tabIndex === 0);
    if (previous) previous.tabIndex = -1;
    event.target.tabIndex = 0;
  };
  days.onclick = event => {
    if (cells.includes(event.target)) showDay(event.target);
  };
  days.onkeydown = event => {
    const direction = {ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1}[event.key];
    if (!direction || !cells.includes(event.target)) return;
    event.preventDefault();
    const next = Math.max(0, Math.min(cells.length - 1, cells.indexOf(event.target) + direction));
    cells[next].focus();
  };
}

select.addEventListener("change", () => {
  quarter = Number(select.value) === current.year ? currentQuarter : 0;
  render();
});
for (const [button, delta] of [[previousPeriod, -1], [nextPeriod, 1]]) {
  button.addEventListener("click", () => {
    quarter += delta;
    render();
    if (button.disabled) (delta < 0 ? nextPeriod : previousPeriod).focus();
  });
}
narrow.addEventListener("change", render);
render();
