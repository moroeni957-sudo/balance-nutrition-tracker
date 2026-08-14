"use strict";

const PROFILE_KEY = "balance.profile.v1";
const RECORD_PREFIX = "balance.record.v1.";
const WATER_GOAL_KEY = "balance.water.goal.v1";
const STEP_COEFFICIENT = 298 / (83 * 7072);
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder";
const PROFILE_PRESETS = Object.freeze({
  Eni: Object.freeze({ mode: "Eni", gender: "male", weight: 83, height: 178, age: 32 }),
  "Бусинка": Object.freeze({ mode: "Бусинка", gender: "female", weight: 63, height: 174, age: 35 }),
});
const MET = {
  cardio: { "Лёгкая": 3, "Умеренная": 5.5, "Высокая": 8 },
  strength: { "Лёгкая": 2, "Умеренная": 4, "Высокая": 6 },
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

function readJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); }
  catch { return null; }
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
  if (!Number.isFinite(value)) return "—";
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
      throw new Error("Некорректная дата записи");
    }
    const totals = Object.fromEntries(NUTRIENTS.map((key) => {
      const value = numberFrom(candidate.menu.totals[key]);
      if (!Number.isFinite(value) || value < 0) throw new Error("Некорректные значения меню");
      return [key, value];
    }));
    const burned = numberFrom(candidate.activity.burned_calories);
    if (!Number.isFinite(burned) || burned < 0) throw new Error("Некорректное значение активности");
    const waterSource = candidate.water || {};
    const waterGoal = numberFrom(waterSource.goal_ml ?? 0);
    const waterConsumed = numberFrom(waterSource.consumed_ml ?? 0);
    if (!Number.isFinite(waterGoal) || waterGoal < 0 || !Number.isFinite(waterConsumed) || waterConsumed < 0) {
      throw new Error("Некорректные значения воды");
    }
    return {
      date,
      menu: { totals },
      activity: { burned_calories: burned },
      water: { goal_ml: waterGoal, consumed_ml: waterConsumed },
    };
  } catch (error) {
    throw new Error(error.message || "Некорректная структура файла");
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
  $("#date-caption").textContent = date === todayISO() ? "Сегодня" : "Выбранная дата";

  if (!hasStoredRecord(date) && tryRepository) {
    try {
      const path = `График меню и активности/Меню и активность ${date}.json`;
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
  $("#water-goal-label").textContent = goal > 0 ? `${format(goal)} мл` : "Норма";
  $("#water-current-value").textContent = `${format(consumed)} мл`;
  // The liquid chamber starts 25 px above the scale bottom and is 230 px tall.
  $("#water-current-label").style.bottom = `${25 + visiblePercent * 2.3}px`;
  $("#water-progress-caption").textContent = goal > 0
    ? `${format(consumed)} из ${format(goal)} мл · ${format(percent)}%`
    : `${format(consumed)} мл · укажите дневную норму`;
  if (document.activeElement !== $("#water-goal")) $("#water-goal").value = goal || "";

  const extra = goal > 0 ? Math.max(0, consumed - goal) : 0;
  const glassCount = Math.floor(extra / 250);
  const overage = $("#water-overage");
  const glasses = $("#water-glasses");
  overage.hidden = glassCount === 0;
  glasses.replaceChildren(...Array.from({ length: glassCount }, (_, index) => {
    const glass = document.createElement("span");
    glass.className = "water-glass";
    glass.setAttribute("aria-label", `Дополнительные ${250 * (index + 1)} мл`);
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
    const goal = getPositive("#water-goal", "Дневная норма", { allowZero: false, integer: true });
    record.water.goal_ml = goal;
    localStorage.setItem(WATER_GOAL_KEY, JSON.stringify(goal));
    saveWater();
    renderWater();
    toast(`Дневная норма воды: ${format(goal)} мл.`);
  } catch (error) { toast(error.message, true); }
}

function addWater() {
  try {
    if (!(record.water.goal_ml > 0)) throw new Error("Сначала укажите дневную норму воды.");
    const amount = getPositive("#water-amount", "Выпито сейчас", { allowZero: false, integer: true });
    record.water.consumed_ml += amount;
    $("#water-amount").value = "";
    saveWater();
    renderWater();
    toast(`Добавлено ${format(amount)} мл воды.`);
  } catch (error) { toast(error.message, true); }
}

function resetWater() {
  if (!(record.water.consumed_ml > 0)) return;
  if (!confirm(`Обнулить выпитую воду за ${displayDate(activeDate)}?`)) return;
  record.water.consumed_ml = 0;
  saveWater();
  renderWater();
  toast("Дневной объём воды обнулён.");
}

function renderSaveState(kind, dirty) {
  const target = $(`#${kind}-save-state`);
  target.textContent = dirty ? "Есть несохранённые изменения" : (hasStoredRecord(activeDate) ? "Сохранено" : "Не сохранено");
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
    status.innerHTML = `Профицит <strong>+${magnitude}</strong> ккал`;
    status.classList.add("surplus");
  } else if (difference < 0) {
    status.innerHTML = `Дефицит <strong>−${magnitude}</strong> ккал`;
    status.classList.add("deficit");
  } else {
    status.innerHTML = `Баланс <strong>0</strong> ккал`;
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
    throw new Error(`Проверьте поле «${label}».`);
  }
  return value;
}

function calculateNutrition(showError = true) {
  try {
    const base = {
      calories: getPositive("#food-calories", "Калорийность"),
      proteins: getPositive("#food-proteins", "Белки"),
      fats: getPositive("#food-fats", "Жиры"),
      carbs: getPositive("#food-carbs", "Углеводы"),
    };
    const portion = getPositive("#food-portion", "Размер порции", { allowZero: false });
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
  toast("Сначала заполните профиль для расчёта активности.", true);
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
      const hours = getPositive("#rest-hours", "Часы");
      const minutes = getPositive("#rest-minutes", "Минуты", { integer: true });
      if (minutes >= 60 || hours + minutes === 0) throw new Error("Укажите время покоя, минуты — от 0 до 59.");
      value = bmr() * (hours + minutes / 60) / 24;
    } else if (kind === "steps") {
      const steps = getPositive("#steps", "Шаги", { allowZero: false, integer: true });
      value = steps * profile.weight * STEP_COEFFICIENT;
    } else {
      const minutes = getPositive(`#${kind}-minutes`, "Минуты", { allowZero: false });
      const intensity = $(`#${kind}-intensity`).value;
      value = MET[kind][intensity] * 3.5 * profile.weight * minutes / 200;
    }
    $(`#${kind}-result strong`).textContent = format(value);
    return value;
  } catch (error) {
    if (showError) toast(error.message, true);
    return null;
  }
}

function activityIsFilled(kind) {
  if (kind === "rest") return Boolean($("#rest-hours").value.trim() || $("#rest-minutes").value.trim());
  if (kind === "steps") return Boolean($("#steps").value.trim());
  return Boolean($(`#${kind}-minutes`).value.trim());
}

function addManual(selector, type) {
  try {
    const value = getPositive(selector, type === "menu" ? "Калории" : "Энергозатраты", { allowZero: false });
    if (type === "menu") {
      record.menu.totals.calories += value;
      menuDirty = true;
    } else {
      record.activity.burned_calories += value;
      activityDirty = true;
    }
    $(selector).value = "";
    render();
  } catch (error) { toast(error.message, true); }
}

function clearNutritionInputs() {
  for (const key of NUTRIENTS) $(`#result-${key}`).textContent = "—";
}

function clearActivityInputs() {
  for (const selector of ["#rest-hours", "#rest-minutes", "#steps", "#cardio-minutes", "#strength-minutes"]) $(selector).value = "";
  $("#cardio-intensity").value = "Умеренная";
  $("#strength-intensity").value = "Умеренная";
  for (const kind of ["rest", "steps", "cardio", "strength"]) $(`#${kind}-result strong`).textContent = "—";
}

function savePart(part) {
  const saved = getStoredRecord(activeDate);
  if (part === "menu") {
    saved.menu = structuredClone(record.menu);
    menuDirty = false;
  } else {
    saved.activity = structuredClone(record.activity);
    activityDirty = false;
  }
  saved.date = activeDate;
  storeRecord(saved);
  render();
  toast(`${part === "menu" ? "Меню" : "Активность"} за ${displayDate(activeDate)} сохранен${part === "menu" ? "о" : "а"}.`);
}

function clearPart(part, savedToo) {
  const label = part === "menu" ? "меню" : "активность";
  if (savedToo && !confirm(`Обнулить ${label} за ${displayDate(activeDate)} в архиве?`)) return;
  if (part === "menu") {
    record.menu = emptyRecord(activeDate).menu;
    menuDirty = !savedToo;
  } else {
    record.activity = emptyRecord(activeDate).activity;
    activityDirty = !savedToo;
  }
  if (savedToo) {
    const saved = getStoredRecord(activeDate);
    saved[part] = structuredClone(record[part]);
    saved.date = activeDate;
    storeRecord(saved);
    if (part === "menu") menuDirty = false; else activityDirty = false;
    toast(`${part === "menu" ? "Меню" : "Активность"} в архиве обнулен${part === "menu" ? "о" : "а"}.`);
  }
  render();
}

function renderProfile() {
  const presetName = profile && Object.hasOwn(PROFILE_PRESETS, profile.mode) ? profile.mode : null;
  $("#profile-summary").textContent = profile
    ? `${presetName ? `${presetName} · ` : ""}${format(profile.weight)} кг · ${profile.age} лет`
    : "Заполните профиль";
  const mode = presetName || "manual";
  $(`input[name="profile-mode"][value="${mode}"]`).checked = true;
  $$('input[name="gender"]').forEach((input) => { input.checked = input.value === profile?.gender; });
  $("#profile-weight").value = profile?.weight ?? "";
  $("#profile-height").value = profile?.height ?? "";
  $("#profile-age").value = profile?.age ?? "";
  setProfileMode(mode);
}

function setProfileMode(mode) {
  const presetSelected = Object.hasOwn(PROFILE_PRESETS, mode);
  const manualFields = $("#manual-profile-fields");
  manualFields.hidden = presetSelected;
  $$('input[name="gender"], #profile-weight, #profile-height, #profile-age', manualFields)
    .forEach((input) => { input.disabled = presetSelected; });
}

function openProfile() {
  renderProfile();
  const dialog = $("#profile-dialog");
  if (!dialog.open) dialog.showModal();
}

function saveProfile(event) {
  event.preventDefault();
  const mode = $('input[name="profile-mode"]:checked')?.value || "manual";
  if (Object.hasOwn(PROFILE_PRESETS, mode)) {
    profile = { ...PROFILE_PRESETS[mode] };
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
    $("#profile-error").textContent = "";
    renderProfile();
    $("#profile-dialog").close();
    toast(`Профиль ${mode} выбран.`);
    return;
  }
  const gender = $('input[name="gender"]:checked')?.value;
  const weight = numberFrom($("#profile-weight").value);
  const height = numberFrom($("#profile-height").value);
  const age = numberFrom($("#profile-age").value);
  if (!["male", "female"].includes(gender) || weight <= 0 || height <= 0 || age <= 0 || !Number.isInteger(age)) {
    $("#profile-error").textContent = "Выберите пол, укажите положительные вес и рост, а возраст — целым числом.";
    return;
  }
  profile = { mode: "manual", gender, weight, height, age };
  localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  $("#profile-error").textContent = "";
  renderProfile();
  $("#profile-dialog").close();
  toast("Профиль сохранён.");
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
  if (!configured) throw new Error("Google Drive ещё не настроен: добавьте OAuth Client ID, API key и номер проекта.");
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
  throw new Error("Не удалось загрузить сервисы Google. Проверьте интернет и блокировщик рекламы.");
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
        if (response?.error || !response?.access_token) return reject(new Error(response?.error_description || "Google не выдал разрешение на доступ."));
        driveAccessToken = response.access_token;
        resolve(driveAccessToken);
      };
      driveTokenClient.error_callback = (error) => reject(new Error(error?.message || "Окно авторизации Google было закрыто."));
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
    if (!folder?.id) throw new Error(`Папка профиля ${profile.mode} не настроена.`);
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
      .setTitle(`Выберите папку «${folder.name}»`)
      .addView(view)
      .setCallback((data) => {
        if (data.action === google.picker.Action.CANCEL) reject(new Error("Выбор папки отменён."));
        if (data.action !== google.picker.Action.PICKED) return;
        const selectedId = data.docs?.[0]?.id;
        if (selectedId !== folder.id) return reject(new Error(`Выберите папку «${folder.name}».`));
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
  if (!await canAccessDriveFolder(folder.id)) throw new Error("Папка не предоставлена приложению.");
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
    if (!response.ok) throw new Error(`Google Drive вернул ошибку ${response.status}.`);
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
    const filename = `Меню и активность ${activeDate}.json`;
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
    if (!response.ok) throw new Error(`Не удалось сохранить файл: Google Drive вернул ${response.status}.`);
    storeRecord(record);
    menuDirty = false;
    activityDirty = false;
    render();
    toast(`${filename} загружен в папку «${folder.name}».`);
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
    toast(`Из папки «${folder.name}» загружено записей: ${imported}${skipped ? `, пропущено: ${skipped}` : ""}.`);
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
    toast(`Импортировано записей: ${imported}.`);
  } catch (error) { toast(`Не удалось импортировать JSON: ${error.message}`, true); }
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
  const totalLabel = total > 0 ? `Профицит +${format(total)} ккал` : total < 0 ? `Дефицит −${format(Math.abs(total))} ккал` : "Баланс 0 ккал";
  $("#chart-total").innerHTML = `Баланс за период: <strong>${totalLabel}</strong>`;

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
    toast("Порция добавлена в дневное меню.");
  });
  $("#nutrition-reset").addEventListener("click", clearNutritionInputs);

  $$('[data-calculate]').forEach((button) => button.addEventListener("click", () => calculateActivity(button.dataset.calculate)));
  $("#activity-add").addEventListener("click", () => {
    const kinds = ["rest", "steps", "cardio", "strength"].filter(activityIsFilled);
    if (!kinds.length) return toast("Заполните хотя бы один вид активности.", true);
    let total = 0;
    for (const kind of kinds) {
      const value = calculateActivity(kind);
      if (value === null) return;
      total += value;
    }
    record.activity.burned_calories += total;
    activityDirty = true;
    render();
    toast("Активность добавлена в дневной итог.");
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
    if ((menuDirty || activityDirty) && !confirm("Перейти к другой дате без сохранения текущих изменений?")) {
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
  $("#export-all-button").addEventListener("click", () => downloadJSON({ version: 1, exported_at: new Date().toISOString(), profile, records: allRecords() }, `Архив баланса ${todayISO()}.json`));

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
