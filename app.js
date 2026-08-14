"use strict";

const PROFILE_KEY = "balance.profile.v1";
const RECORD_PREFIX = "balance.record.v1.";
const WATER_GOAL_KEY = "balance.water.goal.v1";
const CHAT_HISTORY_KEY = "balance.codex.history.v1";
const CHAT_SESSION_KEY = "balance.codex.session.v1";
const STEP_COEFFICIENT = 298 / (83 * 7072);
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder";
const PROFILE_PRESETS = Object.freeze({
  Eni: Object.freeze({ mode: "Eni", gender: "male", weight: 83, height: 178, age: 32 }),
  "Ð‘ÑƒÑÐ¸Ð½ÐºÐ°": Object.freeze({ mode: "Ð‘ÑƒÑÐ¸Ð½ÐºÐ°", gender: "female", weight: 63, height: 174, age: 35 }),
});
const MET = {
  cardio: { "Ð›Ñ‘Ð³ÐºÐ°Ñ": 3, "Ð£Ð¼ÐµÑ€ÐµÐ½Ð½Ð°Ñ": 5.5, "Ð’Ñ‹ÑÐ¾ÐºÐ°Ñ": 8 },
  strength: { "Ð›Ñ‘Ð³ÐºÐ°Ñ": 2, "Ð£Ð¼ÐµÑ€ÐµÐ½Ð½Ð°Ñ": 4, "Ð’Ñ‹ÑÐ¾ÐºÐ°Ñ": 6 },
};
const NUTRIENTS = ["calories", "proteins", "fats", "carbs"];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const todayISO = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};
const emptyRecord = (date) => ({
  date,
  menu: { totals: { calories: 0, proteins: 0, fats: 0, carbs: 0 } },
  activity: { burned_calories: 0 },
  water: { goal_ml: savedWaterGoal(), consumed_ml: 0 },
});

let profile = normalizeProfile(readJSON(PROFILE_KEY));
let activeDate = todayISO();
let record = emptyRecord(activeDate);
let menuDirty = false;
let activityDirty = false;
let toastTimer;
let driveAccessToken = null;
let driveTokenClient = null;
let driveAuthPromise = null;
let chatHistory = normalizeChatHistory(readJSON(CHAT_HISTORY_KEY));

function readJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); }
  catch { return null; }
}

function normalizeChatHistory(candidate) {
  if (!Array.isArray(candidate)) return [];
  return candidate
    .filter((item) => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string")
    .map((item) => ({ role: item.role, content: item.content.slice(0, 2000) }))
    .slice(-8);
}

function chatSessionId() {
  let id = localStorage.getItem(CHAT_SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(CHAT_SESSION_KEY, id);
  }
  return id;
}

function chatEndpoint() {
  const endpoint = window.BALANCE_CHAT_CONFIG?.endpoint;
  return typeof endpoint === "string" ? endpoint.trim() : "";
}

function postQueueRequest(endpoint, payload) {
  return new Promise((resolve) => {
    const frameName = `codex-queue-${payload.request_id}`;
    const frame = document.createElement("iframe");
    const form = document.createElement("form");
    const input = document.createElement("input");
    frame.hidden = true;
    frame.name = frameName;
    form.hidden = true;
    form.method = "POST";
    form.action = endpoint;
    form.target = frameName;
    input.type = "hidden";
    input.name = "payload";
    input.value = JSON.stringify(payload);
    form.append(input);
    document.body.append(frame, form);
    frame.addEventListener("load", () => {
      setTimeout(() => {
        form.remove();
        frame.remove();
      }, 1000);
      resolve();
    }, { once: true });
    form.submit();
    setTimeout(resolve, 2000);
  });
}

function queueStatus(endpoint, requestId) {
  return new Promise((resolve, reject) => {
    const callbackName = `balanceCodexStatus_${requestId.replaceAll("-", "_")}`;
    const script = document.createElement("script");
    const timer = setTimeout(() => finish(new Error("Ð¡ÐµÑ€Ð²Ð¸Ñ Ð¾Ñ‡ÐµÑ€ÐµÐ´Ð¸ Ð½Ðµ Ð¾Ñ‚Ð²ÐµÑ‚Ð¸Ð» Ð²Ð¾Ð²Ñ€ÐµÐ¼Ñ.")), 15000);
    function finish(error, value) {
      clearTimeout(timer);
      script.remove();
      delete window[callbackName];
      error ? reject(error) : resolve(value);
    }
    window[callbackName] = (data) => finish(null, data);
    const url = new URL(endpoint);
    url.searchParams.set("action", "status");
    url.searchParams.set("request_id", requestId);
    url.searchParams.set("callback", callbackName);
    url.searchParams.set("_", String(Date.now()));
    script.onerror = () => finish(new Error("ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿Ñ€Ð¾Ð²ÐµÑ€Ð¸Ñ‚ÑŒ Ð¾Ñ‚Ð²ÐµÑ‚ Ð² Ð¾Ñ‡ÐµÑ€ÐµÐ´Ð¸."));
    script.src = url.toString();
    document.head.append(script);
  });
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForQueueAnswer(endpoint, requestId, output) {
  const expiresAt = Date.now() + 15 * 60 * 1000;
  let attempts = 0;
  while (Date.now() < expiresAt) {
    await wait(attempts === 0 ? 2500 : 8000);
    attempts += 1;
    const data = await queueStatus(endpoint, requestId);
    if (data?.status === "completed" && typeof data.answer === "string") return data.answer.trim();
    if (data?.status === "failed") throw new Error(data.error || "Codex Ð½Ðµ ÑÐ¼Ð¾Ð³ Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²Ð¸Ñ‚ÑŒ Ð¾Ñ‚Ð²ÐµÑ‚.");
    output.textContent = data?.status === "processing"
      ? "Ð’Ð°Ñˆ ÐŸÐš Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ð» Ð²Ð¾Ð¿Ñ€Ð¾Ñ. Codex Ð³Ð¾Ñ‚Ð¾Ð²Ð¸Ñ‚ Ð¾Ñ‚Ð²ÐµÑ‚â€¦"
      : "Ð’Ð¾Ð¿Ñ€Ð¾Ñ ÑÐ¾Ñ…Ñ€Ð°Ð½Ñ‘Ð½ Ð½Ð° Google Drive Ð¸ Ð¶Ð´Ñ‘Ñ‚ Ð²ÐºÐ»ÑŽÑ‡Ñ‘Ð½Ð½Ñ‹Ð¹ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ workerâ€¦";
  }
  throw new Error("ÐžÑ‚Ð²ÐµÑ‚ Ð¿Ð¾ÐºÐ° Ð½Ðµ Ð³Ð¾Ñ‚Ð¾Ð². Ð—Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚Ðµ worker Ð½Ð° Ð²Ð°ÑˆÐµÐ¼ ÐŸÐš Ð¸ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²ÑŒÑ‚Ðµ Ð²Ð¾Ð¿Ñ€Ð¾Ñ ÐµÑ‰Ñ‘ Ñ€Ð°Ð·.");
}

async function sendCodexQuestion(event) {
  event.preventDefault();
  const input = $("#codex-chat-input");
  const output = $("#codex-chat-output");
  const button = $("#codex-chat-send");
  const message = input.value.trim();
  const endpoint = chatEndpoint();
  if (!message) return;
  if (!endpoint || endpoint.includes("PASTE_APPS_SCRIPT_URL")) {
    output.className = "codex-chat-output error";
    output.textContent = "ÐžÑ‡ÐµÑ€ÐµÐ´ÑŒ Ð²Ð¾Ð¿Ñ€Ð¾ÑÐ¾Ð² ÐµÑ‰Ñ‘ Ð½Ðµ Ð¿Ð¾Ð´ÐºÐ»ÑŽÑ‡ÐµÐ½Ð°. ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ Ð¿Ð¾Ð·Ð¶Ðµ.";
    return;
  }

  button.disabled = true;
  input.disabled = true;
  output.className = "codex-chat-output loading";
  output.textContent = "Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð²Ð¾Ð¿Ñ€Ð¾Ñ Ð² Ð¾Ñ‡ÐµÑ€ÐµÐ´ÑŒ Google Driveâ€¦";
  try {
    const requestId = crypto.randomUUID();
    await postQueueRequest(endpoint, {
      action: "enqueue",
      request_id: requestId,
      session_id: chatSessionId(),
      message,
      history: chatHistory,
      source: location.origin,
    });
    const answer = await waitForQueueAnswer(endpoint, requestId, output);
    if (!answer) throw new Error("Codex Ð½Ðµ Ð²ÐµÑ€Ð½ÑƒÐ» Ñ‚ÐµÐºÑÑ‚ Ð¾Ñ‚Ð²ÐµÑ‚Ð°.");
    chatHistory = [...chatHistory, { role: "user", content: message }, { role: "assistant", content: answer }].slice(-8);
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(chatHistory));
    output.className = "codex-chat-output";
    output.textContent = answer;
    input.value = "";
  } catch (error) {
    output.className = "codex-chat-output error";
    output.textContent = error.message || "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ñ‚ÑŒ Ð¾Ñ‚Ð²ÐµÑ‚. ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ ÐµÑ‰Ñ‘ Ñ€Ð°Ð·.";
  } finally {
    button.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

function numberFrom(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : NaN;
  return Number(String(value ?? "").trim().replace(",", "."));
}

function savedWaterGoal() {
  const value = numberFrom(readJSON(WATER_GOAL_KEY));
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function normalizeProfile(candidate) {
  if (!candidate || typeof candidate !== "object") return null;
  if (Object.hasOwn(PROFILE_PRESETS, candidate.mode)) return { ...PROFILE_PRESETS[candidate.mode] };
  const gender = candidate.gender;
  const weight = numberFrom(candidate.weight);
  const height = numberFrom(candidate.height);
  const age = numberFrom(candidate.age);
  if (!["male", "female"].includes(gender) || weight <= 0 || height <= 0 || age <= 0 || !Number.isInteger(age)) return null;
  return { mode: "manual", gender, weight, height, age };
}

function format(value) {
  if (!Number.isFinite(value)) return "â€”";
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Math.round(value * 100) / 100);
}

function displayDate(iso) {
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year}`;
}

function validRecord(candidate, fallbackDate) {
  try {
    const date = String(candidate.date || fallbackDate || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(new Date(`${date}T12:00:00`).getTime())) {
      throw new Error("ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð°Ñ Ð´Ð°Ñ‚Ð° Ð·Ð°Ð¿Ð¸ÑÐ¸");
    }
    const totals = Object.fromEntries(NUTRIENTS.map((key) => {
      const value = numberFrom(candidate.menu.totals[key]);
      if (!Number.isFinite(value) || value < 0) throw new Error("ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ñ Ð¼ÐµÐ½ÑŽ");
      return [key, value];
    }));
    const burned = numberFrom(candidate.activity.burned_calories);
    if (!Number.isFinite(burned) || burned < 0) throw new Error("ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð¾Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸");
    const waterSource = candidate.water || {};
    const waterGoal = numberFrom(waterSource.goal_ml ?? 0);
    const waterConsumed = numberFrom(waterSource.consumed_ml ?? 0);
    if (!Number.isFinite(waterGoal) || waterGoal < 0 || !Number.isFinite(waterConsumed) || waterConsumed < 0) {
      throw new Error("ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ñ Ð²Ð¾Ð´Ñ‹");
    }
    return {
      date,
      menu: { totals },
      activity: { burned_calories: burned },
      water: { goal_ml: waterGoal, consumed_ml: waterConsumed },
    };
  } catch (error) {
    throw new Error(error.message || "ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð°Ñ ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð° Ñ„Ð°Ð¹Ð»Ð°");
  }
}

function storageKey(date) { return `${RECORD_PREFIX}${date}`; }
function hasStoredRecord(date) { return localStorage.getItem(storageKey(date)) !== null; }
function getStoredRecord(date) {
  const saved = readJSON(storageKey(date));
  if (!saved) return emptyRecord(date);
  try { return validRecord(saved, date); }
  catch { return emptyRecord(date); }
}
function storeRecord(value) { localStorage.setItem(storageKey(value.date), JSON.stringify(value)); }

async function loadDate(date, tryRepository = false) {
  activeDate = date;
  $("#active-date").value = date;
  $("#date-caption").textContent = date === todayISO() ? "Ð¡ÐµÐ³Ð¾Ð´Ð½Ñ" : "Ð’Ñ‹Ð±Ñ€Ð°Ð½Ð½Ð°Ñ Ð´Ð°Ñ‚Ð°";

  if (!hasStoredRecord(date) && tryRepository) {
    try {
      const path = `Ð“Ñ€Ð°Ñ„Ð¸Ðº Ð¼ÐµÐ½ÑŽ Ð¸ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸/ÐœÐµÐ½ÑŽ Ð¸ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ÑŒ ${date}.json`;
      const response = await fetch(path);
      if (response.ok) {
        const imported = validRecord(await response.json(), date);
        imported.date = date;
        storeRecord(imported);
      }
    } catch { /* GitHub Pages or local file may not contain a legacy record. */ }
  }

  record = getStoredRecord(date);
  record.date = date;
  menuDirty = false;
  activityDirty = false;
  render();
}

function render() {
  for (const key of NUTRIENTS) $(`#daily-${key}`).textContent = format(record.menu.totals[key]);
  $("#daily-activity").textContent = format(record.activity.burned_calories);
  $("#balance-menu").textContent = format(record.menu.totals.calories);
  $("#balance-activity").textContent = format(record.activity.burned_calories);
  renderBalance();
  renderWater();
  renderSaveState("menu", menuDirty);
  renderSaveState("activity", activityDirty);
}

function renderWater() {
  const goal = record.water?.goal_ml || 0;
  const consumed = record.water?.consumed_ml || 0;
  const percent = goal > 0 ? consumed / goal * 100 : 0;
  const visiblePercent = Math.max(0, Math.min(100, percent));
  const fill = $("#water-fill");
  const bottle = $("#water-bottle");
  fill.style.height = `${visiblePercent}%`;
  bottle.setAttribute("aria-valuenow", String(consumed));
  bottle.setAttribute("aria-valuemax", String(goal || 1));
  $("#water-goal-label").textContent = goal > 0 ? `${format(goal)} Ð¼Ð»` : "ÐÐ¾Ñ€Ð¼Ð°";
  $("#water-current-value").textContent = `${format(consumed)} Ð¼Ð»`;
  // The liquid chamber starts 25 px above the scale bottom and is 230 px tall.
  $("#water-current-label").style.bottom = `${25 + visiblePercent * 2.3}px`;
  $("#water-progress-caption").textContent = goal > 0
    ? `${format(consumed)} Ð¸Ð· ${format(goal)} Ð¼Ð» Â· ${format(percent)}%`
    : `${format(consumed)} Ð¼Ð» Â· ÑƒÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ð´Ð½ÐµÐ²Ð½ÑƒÑŽ Ð½Ð¾Ñ€Ð¼Ñƒ`;
  if (document.activeElement !== $("#water-goal")) $("#water-goal").value = goal || "";

  const extra = goal > 0 ? Math.max(0, consumed - goal) : 0;
  const glassCount = Math.floor(extra / 250);
  const overage = $("#water-overage");
  const glasses = $("#water-glasses");
  overage.hidden = glassCount === 0;
  glasses.replaceChildren(...Array.from({ length: glassCount }, (_, index) => {
    const glass = document.createElement("span");
    glass.className = "water-glass";
    glass.setAttribute("aria-label", `Ð”Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ ${250 * (index + 1)} Ð¼Ð»`);
    glass.innerHTML = "<i></i><small>+250</small>";
    return glass;
  }));
}

function saveWater() {
  const saved = getStoredRecord(activeDate);
  saved.water = structuredClone(record.water);
  saved.date = activeDate;
  storeRecord(saved);
}

function updateWaterGoal() {
  try {
    const goal = getPositive("#water-goal", "Ð”Ð½ÐµÐ²Ð½Ð°Ñ Ð½Ð¾Ñ€Ð¼Ð°", { allowZero: false, integer: true });
    record.water.goal_ml = goal;
    localStorage.setItem(WATER_GOAL_KEY, JSON.stringify(goal));
    saveWater();
    renderWater();
    toast(`Ð”Ð½ÐµÐ²Ð½Ð°Ñ Ð½Ð¾Ñ€Ð¼Ð° Ð²Ð¾Ð´Ñ‹: ${format(goal)} Ð¼Ð».`);
  } catch (error) { toast(error.message, true); }
}

function addWater() {
  try {
    if (!(record.water.goal_ml > 0)) throw new Error("Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° ÑƒÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ð´Ð½ÐµÐ²Ð½ÑƒÑŽ Ð½Ð¾Ñ€Ð¼Ñƒ Ð²Ð¾Ð´Ñ‹.");
    const amount = getPositive("#water-amount", "Ð’Ñ‹Ð¿Ð¸Ñ‚Ð¾ ÑÐµÐ¹Ñ‡Ð°Ñ", { allowZero: false, integer: true });
    record.water.consumed_ml += amount;
    $("#water-amount").value = "";
    saveWater();
    renderWater();
    toast(`Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ ${format(amount)} Ð¼Ð» Ð²Ð¾Ð´Ñ‹.`);
  } catch (error) { toast(error.message, true); }
}

function resetWater() {
  if (!(record.water.consumed_ml > 0)) return;
  if (!confirm(`ÐžÐ±Ð½ÑƒÐ»Ð¸Ñ‚ÑŒ Ð²Ñ‹Ð¿Ð¸Ñ‚ÑƒÑŽ Ð²Ð¾Ð´Ñƒ Ð·Ð° ${displayDate(activeDate)}?`)) return;
  record.water.consumed_ml = 0;
  saveWater();
  renderWater();
  toast("Ð”Ð½ÐµÐ²Ð½Ð¾Ð¹ Ð¾Ð±ÑŠÑ‘Ð¼ Ð²Ð¾Ð´Ñ‹ Ð¾Ð±Ð½ÑƒÐ»Ñ‘Ð½.");
}

function renderSaveState(kind, dirty) {
  const target = $(`#${kind}-save-state`);
  target.textContent = dirty ? "Ð•ÑÑ‚ÑŒ Ð½ÐµÑÐ¾Ñ…Ñ€Ð°Ð½Ñ‘Ð½Ð½Ñ‹Ðµ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ" : (hasStoredRecord(activeDate) ? "Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¾" : "ÐÐµ ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¾");
  target.classList.toggle("saved", !dirty && hasStoredRecord(activeDate));
}

function renderBalance() {
  const consumed = record.menu.totals.calories;
  const burned = record.activity.burned_calories;
  const difference = consumed - burned;
  const magnitude = format(Math.abs(difference));
  const status = $("#balance-status");
  status.className = "balance-status";
  if (difference > 0) {
    status.innerHTML = `ÐŸÑ€Ð¾Ñ„Ð¸Ñ†Ð¸Ñ‚ <strong>+${magnitude}</strong> ÐºÐºÐ°Ð»`;
    status.classList.add("surplus");
  } else if (difference < 0) {
    status.innerHTML = `Ð”ÐµÑ„Ð¸Ñ†Ð¸Ñ‚ <strong>âˆ’${magnitude}</strong> ÐºÐºÐ°Ð»`;
    status.classList.add("deficit");
  } else {
    status.innerHTML = `Ð‘Ð°Ð»Ð°Ð½Ñ <strong>0</strong> ÐºÐºÐ°Ð»`;
    status.classList.add("neutral");
  }
  const normalized = Math.max(-1, Math.min(1, difference / Math.max(consumed, burned, 1)));
  $("#balance-gauge .gauge-needle").style.transform = `rotate(${normalized * 90 - 90}deg)`;
}

function toast(message, isError = false) {
  const node = $("#toast");
  clearTimeout(toastTimer);
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("visible");
  toastTimer = setTimeout(() => node.classList.remove("visible"), 3200);
}

function getPositive(selector, label, { allowZero = true, integer = false } = {}) {
  const input = $(selector);
  const value = numberFrom(input.value);
  const valid = Number.isFinite(value) && value >= 0 && (allowZero || value > 0) && (!integer || Number.isInteger(value));
  if (!valid) {
    input.focus();
    throw new Error(`ÐŸÑ€Ð¾Ð²ÐµÑ€ÑŒÑ‚Ðµ Ð¿Ð¾Ð»Ðµ Â«${label}Â».`);
  }
  return value;
}

function calculateNutrition(showError = true) {
  try {
    const base = {
      calories: getPositive("#food-calories", "ÐšÐ°Ð»Ð¾Ñ€Ð¸Ð¹Ð½Ð¾ÑÑ‚ÑŒ"),
      proteins: getPositive("#food-proteins", "Ð‘ÐµÐ»ÐºÐ¸"),
      fats: getPositive("#food-fats", "Ð–Ð¸Ñ€Ñ‹"),
      carbs: getPositive("#food-carbs", "Ð£Ð³Ð»ÐµÐ²Ð¾Ð´Ñ‹"),
    };
    const portion = getPositive("#food-portion", "Ð Ð°Ð·Ð¼ÐµÑ€ Ð¿Ð¾Ñ€Ñ†Ð¸Ð¸", { allowZero: false });
    const result = Object.fromEntries(NUTRIENTS.map((key) => [key, base[key] * portion / 100]));
    for (const key of NUTRIENTS) $(`#result-${key}`).textContent = format(result[key]);
    return result;
  } catch (error) {
    if (showError) toast(error.message, true);
    return null;
  }
}

function ensureProfile() {
  if (profile && profile.weight > 0 && profile.height > 0 && profile.age > 0 && ["male", "female"].includes(profile.gender)) return true;
  openProfile();
  toast("Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° Ð·Ð°Ð¿Ð¾Ð»Ð½Ð¸Ñ‚Ðµ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸.", true);
  return false;
}

function bmr() {
  if (profile.gender === "male") return 88.36 + 13.4 * profile.weight + 4.8 * profile.height - 5.7 * profile.age;
  return 447.6 + 9.2 * profile.weight + 3.1 * profile.height - 4.3 * profile.age;
}

function calculateActivity(kind, showError = true) {
  try {
    if (!ensureProfile()) return null;
    let value;
    if (kind === "rest") {
      const hours = getPositive("#rest-hours", "Ð§Ð°ÑÑ…1380 tokens truncated…me="gender"]:checked')?.value;
  const weight = numberFrom($("#profile-weight").value);
  const height = numberFrom($("#profile-height").value);
  const age = numberFrom($("#profile-age").value);
  if (!["male", "female"].includes(gender) || weight <= 0 || height <= 0 || age <= 0 || !Number.isInteger(age)) {
    $("#profile-error").textContent = "Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ð¿Ð¾Ð», ÑƒÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ð¿Ð¾Ð»Ð¾Ð¶Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ Ð²ÐµÑ Ð¸ Ñ€Ð¾ÑÑ‚, Ð° Ð²Ð¾Ð·Ñ€Ð°ÑÑ‚ â€” Ñ†ÐµÐ»Ñ‹Ð¼ Ñ‡Ð¸ÑÐ»Ð¾Ð¼.";
    return;
  }
  profile = { mode: "manual", gender, weight, height, age };
  localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  $("#profile-error").textContent = "";
  renderProfile();
  $("#profile-dialog").close();
  toast("ÐŸÑ€Ð¾Ñ„Ð¸Ð»ÑŒ ÑÐ¾Ñ…Ñ€Ð°Ð½Ñ‘Ð½.");
}

function downloadJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function driveConfig() {
  const config = window.GOOGLE_DRIVE_CONFIG || {};
  const configured = config.clientId && config.apiKey && config.appId && config.folderId
    && !Object.values(config).some((value) => String(value).startsWith("PASTE_"));
  if (!configured) throw new Error("Google Drive ÐµÑ‰Ñ‘ Ð½Ðµ Ð½Ð°ÑÑ‚Ñ€Ð¾ÐµÐ½: Ð´Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ OAuth Client ID, API key Ð¸ Ð½Ð¾Ð¼ÐµÑ€ Ð¿Ñ€Ð¾ÐµÐºÑ‚Ð°.");
  return config;
}

async function waitForGoogleApi(name, timeout = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (name === "identity" && window.google?.accounts?.oauth2) return;
    if (name === "picker" && window.gapi) {
      await new Promise((resolve, reject) => window.gapi.load("picker", { callback: resolve, onerror: reject }));
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ ÑÐµÑ€Ð²Ð¸ÑÑ‹ Google. ÐŸÑ€Ð¾Ð²ÐµÑ€ÑŒÑ‚Ðµ Ð¸Ð½Ñ‚ÐµÑ€Ð½ÐµÑ‚ Ð¸ Ð±Ð»Ð¾ÐºÐ¸Ñ€Ð¾Ð²Ñ‰Ð¸Ðº Ñ€ÐµÐºÐ»Ð°Ð¼Ñ‹.");
}

async function authorizeDrive() {
  if (driveAccessToken) return driveAccessToken;
  if (driveAuthPromise) return driveAuthPromise;
  const config = driveConfig();
  driveAuthPromise = (async () => {
    await waitForGoogleApi("identity");
    return new Promise((resolve, reject) => {
      if (!driveTokenClient) {
        driveTokenClient = google.accounts.oauth2.initTokenClient({
          client_id: config.clientId,
          scope: DRIVE_SCOPE,
          callback: () => {},
          error_callback: () => {},
        });
      }
      driveTokenClient.callback = (response) => {
        if (response?.error || !response?.access_token) return reject(new Error(response?.error_description || "Google Ð½Ðµ Ð²Ñ‹Ð´Ð°Ð» Ñ€Ð°Ð·Ñ€ÐµÑˆÐµÐ½Ð¸Ðµ Ð½Ð° Ð´Ð¾ÑÑ‚ÑƒÐ¿."));
        driveAccessToken = response.access_token;
        resolve(driveAccessToken);
      };
      driveTokenClient.error_callback = (error) => reject(new Error(error?.message || "ÐžÐºÐ½Ð¾ Ð°Ð²Ñ‚Ð¾Ñ€Ð¸Ð·Ð°Ñ†Ð¸Ð¸ Google Ð±Ñ‹Ð»Ð¾ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð¾."));
      // Google remembers the grant for this account and OAuth client.
      // An empty prompt asks for consent only when it has not been granted yet.
      driveTokenClient.requestAccessToken({ prompt: "" });
    });
  })();
  try { return await driveAuthPromise; }
  finally { driveAuthPromise = null; }
}

async function driveFetch(url, options = {}) {
  const token = await authorizeDrive();
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) driveAccessToken = null;
  return response;
}

function driveFolderForProfile() {
  const config = driveConfig();
  if (profile && Object.hasOwn(PROFILE_PRESETS, profile.mode)) {
    const folder = config.profileFolders?.[profile.mode];
    if (!folder?.id) throw new Error(`ÐŸÐ°Ð¿ÐºÐ° Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ñ ${profile.mode} Ð½Ðµ Ð½Ð°ÑÑ‚Ñ€Ð¾ÐµÐ½Ð°.`);
    return { id: folder.id, name: folder.name || profile.mode, url: folder.url || "" };
  }
  return { id: config.folderId, name: config.folderName, url: config.folderUrl };
}

async function canAccessDriveFolder(folderId) {
  const response = await driveFetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(folderId)}?fields=id,name,mimeType&supportsAllDrives=true`);
  return response.ok;
}

async function selectProjectFolder(folder) {
  const config = driveConfig();
  await waitForGoogleApi("picker");
  return new Promise((resolve, reject) => {
    const view = new google.picker.DocsView(google.picker.ViewId.FOLDERS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(true)
      .setMimeTypes(DRIVE_FOLDER_MIME);
    const picker = new google.picker.PickerBuilder()
      .setAppId(config.appId)
      .setDeveloperKey(config.apiKey)
      .setOAuthToken(driveAccessToken)
      .setTitle(`Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ð¿Ð°Ð¿ÐºÑƒ Â«${folder.name}Â»`)
      .addView(view)
      .setCallback((data) => {
        if (data.action === google.picker.Action.CANCEL) reject(new Error("Ð’Ñ‹Ð±Ð¾Ñ€ Ð¿Ð°Ð¿ÐºÐ¸ Ð¾Ñ‚Ð¼ÐµÐ½Ñ‘Ð½."));
        if (data.action !== google.picker.Action.PICKED) return;
        const selectedId = data.docs?.[0]?.id;
        if (selectedId !== folder.id) return reject(new Error(`Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ð¿Ð°Ð¿ÐºÑƒ Â«${folder.name}Â».`));
        resolve(selectedId);
      })
      .build();
    picker.setVisible(true);
  });
}

async function ensureDriveFolderAccess() {
  const folder = driveFolderForProfile();
  await authorizeDrive();
  if (await canAccessDriveFolder(folder.id)) return folder;
  await selectProjectFolder(folder);
  if (!await canAccessDriveFolder(folder.id)) throw new Error("ÐŸÐ°Ð¿ÐºÐ° Ð½Ðµ Ð¿Ñ€ÐµÐ´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Ð° Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸ÑŽ.");
  return folder;
}

async function listDriveJsonFiles(folderId, fileName = null) {
  const clauses = [
    `'${folderId.replaceAll("'", "\\'")}' in parents`,
    "trashed = false",
    "mimeType = 'application/json'",
  ];
  if (fileName) clauses.push(`name = '${fileName.replaceAll("'", "\\'")}'`);
  const found = [];
  let pageToken = "";
  do {
    const params = new URLSearchParams({ q: clauses.join(" and "), fields: "nextPageToken,files(id,name,modifiedTime)", pageSize: "1000", orderBy: "modifiedTime desc" });
    if (pageToken) params.set("pageToken", pageToken);
    const response = await driveFetch(`https://www.googleapis.com/drive/v3/files?${params}`);
    if (!response.ok) throw new Error(`Google Drive Ð²ÐµÑ€Ð½ÑƒÐ» Ð¾ÑˆÐ¸Ð±ÐºÑƒ ${response.status}.`);
    const data = await response.json();
    found.push(...(data.files || []));
    pageToken = data.nextPageToken || "";
  } while (pageToken);
  return found;
}

async function exportCurrentDayToDrive() {
  const button = $("#export-button");
  button.disabled = true;
  try {
    const folder = await ensureDriveFolderAccess();
    const filename = `ÐœÐµÐ½ÑŽ Ð¸ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ÑŒ ${activeDate}.json`;
    const existing = (await listDriveJsonFiles(folder.id, filename))[0];
    const content = JSON.stringify(record, null, 2);
    let response;
    if (existing) {
      response = await driveFetch(`https://www.googleapis.com/upload/drive/v3/files/${existing.id}?uploadType=media&fields=id,name,modifiedTime`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json;charset=utf-8" },
        body: content,
      });
    } else {
      const boundary = `balance_${crypto.randomUUID().replaceAll("-", "")}`;
      const body = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({ name: filename, mimeType: "application/json", parents: [folder.id] })}\r\n--${boundary}\r\nContent-Type: application/json;charset=utf-8\r\n\r\n${content}\r\n--${boundary}--`;
      response = await driveFetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,modifiedTime", {
        method: "POST",
        headers: { "Content-Type": `multipart/related; boundary=${boundary}` },
        body,
      });
    }
    if (!response.ok) throw new Error(`ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ ÑÐ¾Ñ…Ñ€Ð°Ð½Ð¸Ñ‚ÑŒ Ñ„Ð°Ð¹Ð»: Google Drive Ð²ÐµÑ€Ð½ÑƒÐ» ${response.status}.`);
    storeRecord(record);
    menuDirty = false;
    activityDirty = false;
    render();
    toast(`${filename} Ð·Ð°Ð³Ñ€ÑƒÐ¶ÐµÐ½ Ð² Ð¿Ð°Ð¿ÐºÑƒ Â«${folder.name}Â».`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function importArchiveFromDrive() {
  const button = $("#import-button");
  button.disabled = true;
  try {
    const folder = await ensureDriveFolderAccess();
    const files = await listDriveJsonFiles(folder.id);
    let imported = 0;
    let skipped = 0;
    for (const file of files) {
      try {
        const response = await driveFetch(`https://www.googleapis.com/drive/v3/files/${file.id}?alt=media`);
        if (!response.ok) throw new Error();
        const normalized = validRecord(await response.json(), activeDate);
        storeRecord(normalized);
        imported += 1;
      } catch { skipped += 1; }
    }
    await loadDate(activeDate);
    drawChart();
    toast(`Ð˜Ð· Ð¿Ð°Ð¿ÐºÐ¸ Â«${folder.name}Â» Ð·Ð°Ð³Ñ€ÑƒÐ¶ÐµÐ½Ð¾ Ð·Ð°Ð¿Ð¸ÑÐµÐ¹: ${imported}${skipped ? `, Ð¿Ñ€Ð¾Ð¿ÑƒÑ‰ÐµÐ½Ð¾: ${skipped}` : ""}.`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

function allRecords() {
  const records = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (!key?.startsWith(RECORD_PREFIX)) continue;
    try { records.push(validRecord(JSON.parse(localStorage.getItem(key)), key.slice(RECORD_PREFIX.length))); }
    catch { /* Ignore damaged entries. */ }
  }
  return records.sort((a, b) => a.date.localeCompare(b.date));
}

async function importJSON(file) {
  try {
    const data = JSON.parse(await file.text());
    let imported = 0;
    let importedDate = activeDate;
    if (Array.isArray(data.records)) {
      for (const item of data.records) {
        const normalized = validRecord(item, item.date);
        storeRecord(normalized);
        imported += 1;
      }
      if (data.profile) {
        const candidate = normalizeProfile(data.profile);
        if (candidate) {
          profile = candidate;
          localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
          renderProfile();
        }
      }
    } else {
      const normalized = validRecord(data, data.date || activeDate);
      storeRecord(normalized);
      importedDate = normalized.date;
      imported = 1;
    }
    await loadDate(importedDate);
    toast(`Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¾ Ð·Ð°Ð¿Ð¸ÑÐµÐ¹: ${imported}.`);
  } catch (error) { toast(`ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ JSON: ${error.message}`, true); }
}

function recordsForChart(days) {
  const byDate = new Map(allRecords().map((item) => [item.date, item.menu.totals.calories - item.activity.burned_calories]));
  const end = new Date(`${todayISO()}T12:00:00`);
  return Array.from({ length: days }, (_, index) => {
    const date = new Date(end);
    date.setDate(end.getDate() - (days - 1 - index));
    const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    return { date: iso, value: byDate.get(iso) || 0 };
  });
}

function drawChart() {
  const canvas = $("#balance-chart");
  const wrapper = canvas.parentElement;
  const mobileScrollbarReserve = window.innerWidth <= 350 ? 16 : 0;
  const width = Math.max(210, Math.floor(wrapper.clientWidth - mobileScrollbarReserve));
  const height = width < 430 ? 210 : 420;
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * pixelRatio);
  canvas.height = Math.round(height * pixelRatio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d");
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  const days = Number($("#chart-period").value);
  const history = recordsForChart(days);
  const total = history.reduce((sum, item) => sum + item.value, 0);
  const totalLabel = total > 0 ? `ÐŸÑ€Ð¾Ñ„Ð¸Ñ†Ð¸Ñ‚ +${format(total)} ÐºÐºÐ°Ð»` : total < 0 ? `Ð”ÐµÑ„Ð¸Ñ†Ð¸Ñ‚ âˆ’${format(Math.abs(total))} ÐºÐºÐ°Ð»` : "Ð‘Ð°Ð»Ð°Ð½Ñ 0 ÐºÐºÐ°Ð»";
  $("#chart-total").innerHTML = `Ð‘Ð°Ð»Ð°Ð½Ñ Ð·Ð° Ð¿ÐµÑ€Ð¸Ð¾Ð´: <strong>${totalLabel}</strong>`;

  const compact = width < 430;
  const plot = { left: compact ? 43 : 58, right: width - (compact ? 10 : 22), top: 26, bottom: height - 45 };
  const max = 1500;
  context.clearRect(0, 0, width, height);
  context.font = `${compact ? 9 : 11}px Manrope, sans-serif`;
  context.textBaseline = "middle";
  for (let tick = -1500; tick <= 1500; tick += 500) {
    const y = plot.bottom - ((tick + max) / (max * 2)) * (plot.bottom - plot.top);
    context.beginPath();
    context.moveTo(plot.left, y);
    context.lineTo(plot.right, y);
    context.strokeStyle = tick === 0 ? "#9aa39c" : "#e3e7e1";
    context.lineWidth = tick === 0 ? 1.5 : 1;
    context.stroke();
    context.fillStyle = "#7b847e";
    context.textAlign = "right";
    context.fillText(String(tick), plot.left - 9, y);
  }
  const slot = (plot.right - plot.left) / days;
  const bar = Math.max(1.5, slot * .68);
  const zeroY = (plot.top + plot.bottom) / 2;
  const labelStep = compact
    ? ({ 7: 2, 30: 6, 90: 18, 180: 36 })[days]
    : ({ 7: 1, 30: 5, 90: 15, 180: 30 })[days];
  history.forEach((item, index) => {
    const value = Math.max(-max, Math.min(max, item.value));
    const x = plot.left + (index + .5) * slot;
    const y = zeroY - (value / max) * ((plot.bottom - plot.top) / 2);
    context.fillStyle = item.value >= 0 ? "#215c42" : "#e96f51";
    context.fillRect(x - bar / 2, Math.min(zeroY, y), bar, Math.max(1, Math.abs(y - zeroY)));
    if (index % labelStep === 0 || index === days - 1) {
      const [, month, day] = item.date.split("-");
      context.fillStyle = "#7b847e";
      context.textAlign = "center";
      context.fillText(`${day}.${month}`, x, plot.bottom + 20);
    }
  });
}

function bindEvents() {
  $("#codex-chat-form").addEventListener("submit", sendCodexQuestion);

  $("#water-goal").addEventListener("change", updateWaterGoal);
  $("#water-goal").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); updateWaterGoal(); }
  });
  $("#water-add").addEventListener("click", addWater);
  $("#water-amount").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addWater(); }
  });
  $("#water-reset").addEventListener("click", resetWater);

  $("#nutrition-form").addEventListener("submit", (event) => { event.preventDefault(); calculateNutrition(); });
  $("#nutrition-form").addEventListener("reset", () => setTimeout(clearNutritionInputs));
  $("#nutrition-add").addEventListener("click", () => {
    const calculated = calculateNutrition();
    if (!calculated) return;
    for (const key of NUTRIENTS) record.menu.totals[key] += calculated[key];
    menuDirty = true;
    render();
    toast("ÐŸÐ¾Ñ€Ñ†Ð¸Ñ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð° Ð² Ð´Ð½ÐµÐ²Ð½Ð¾Ðµ Ð¼ÐµÐ½ÑŽ.");
  });
  $("#nutrition-reset").addEventListener("click", clearNutritionInputs);

  $$('[data-calculate]').forEach((button) => button.addEventListener("click", () => calculateActivity(button.dataset.calculate)));
  $("#activity-add").addEventListener("click", () => {
    const kinds = ["rest", "steps", "cardio", "strength"].filter(activityIsFilled);
    if (!kinds.length) return toast("Ð—Ð°Ð¿Ð¾Ð»Ð½Ð¸Ñ‚Ðµ Ñ…Ð¾Ñ‚Ñ Ð±Ñ‹ Ð¾Ð´Ð¸Ð½ Ð²Ð¸Ð´ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸.", true);
    let total = 0;
    for (const kind of kinds) {
      const value = calculateActivity(kind);
      if (value === null) return;
      total += value;
    }
    record.activity.burned_calories += total;
    activityDirty = true;
    render();
    toast("ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ÑŒ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð° Ð² Ð´Ð½ÐµÐ²Ð½Ð¾Ð¹ Ð¸Ñ‚Ð¾Ð³.");
  });
  $("#activity-reset").addEventListener("click", clearActivityInputs);

  $("#manual-menu-add").addEventListener("click", () => addManual("#manual-menu", "menu"));
  $("#manual-activity-add").addEventListener("click", () => addManual("#manual-activity", "activity"));
  $("#manual-menu").addEventListener("keydown", (event) => { if (event.key === "Enter") addManual("#manual-menu", "menu"); });
  $("#manual-activity").addEventListener("keydown", (event) => { if (event.key === "Enter") addManual("#manual-activity", "activity"); });

  $("#save-menu").addEventListener("click", () => savePart("menu"));
  $("#save-activity").addEventListener("click", () => savePart("activity"));
  $("#clear-menu-screen").addEventListener("click", () => clearPart("menu", false));
  $("#clear-activity-screen").addEventListener("click", () => clearPart("activity", false));

  $("#active-date").addEventListener("change", async (event) => {
    const nextDate = event.target.value;
    if (!nextDate) return;
    if ((menuDirty || activityDirty) && !confirm("ÐŸÐµÑ€ÐµÐ¹Ñ‚Ð¸ Ðº Ð´Ñ€ÑƒÐ³Ð¾Ð¹ Ð´Ð°Ñ‚Ðµ Ð±ÐµÐ· ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¸Ñ Ñ‚ÐµÐºÑƒÑ‰Ð¸Ñ… Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹?")) {
      event.target.value = activeDate;
      return;
    }
    await loadDate(nextDate, true);
  });

  $("#profile-button").addEventListener("click", openProfile);
  $("#profile-form").addEventListener("submit", saveProfile);
  $$('input[name="profile-mode"]').forEach((input) => input.addEventListener("change", (event) => setProfileMode(event.target.value)));
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));

  $("#import-button").addEventListener("click", importArchiveFromDrive);
  $("#export-button").addEventListener("click", exportCurrentDayToDrive);
  $("#export-all-button").addEventListener("click", () => downloadJSON({ version: 1, exported_at: new Date().toISOString(), profile, records: allRecords() }, `ÐÑ€Ñ…Ð¸Ð² Ð±Ð°Ð»Ð°Ð½ÑÐ° ${todayISO()}.json`));

  $("#chart-button").addEventListener("click", () => { $("#chart-dialog").showModal(); drawChart(); });
  $("#chart-period").addEventListener("change", drawChart);
  window.addEventListener("beforeunload", (event) => {
    if (!menuDirty && !activityDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function init() {
  bindEvents();
  renderProfile();
  await loadDate(activeDate, true);
  if (!profile) openProfile();
}

init();

