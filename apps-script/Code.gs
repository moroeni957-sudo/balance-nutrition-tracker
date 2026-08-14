const QUEUE_FOLDER_ID = "1SrGlAupJAL6woTwFyFcWFu7u_ACUTQKf";
const QUESTION_PREFIX = "question_";
const ANSWER_PREFIX = "answer_";
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function doPost(event) {
  try {
    const payload = parsePayload_(event);
    if (payload.action !== "enqueue") throw new Error("Unknown action");
    const requestId = validateRequestId_(payload.request_id);
    const message = String(payload.message || "").trim();
    if (!message || message.length > 1000) throw new Error("Question must contain 1â€“1000 characters");

    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      const folder = DriveApp.getFolderById(QUEUE_FOLDER_ID);
      const name = QUESTION_PREFIX + requestId + ".json";
      if (!folder.getFilesByName(name).hasNext()) {
        const history = Array.isArray(payload.history) ? payload.history.slice(-8) : [];
        const question = {
          id: requestId,
          status: "pending",
          created_at: new Date().toISOString(),
          session_id: String(payload.session_id || "").slice(0, 80),
          message: message,
          history: history,
          source: String(payload.source || "").slice(0, 200)
        };
        folder.createFile(name, JSON.stringify(question, null, 2), MimeType.PLAIN_TEXT)
          .setDescription("Public Codex question queue item");
      }
    } finally {
      lock.releaseLock();
    }
    return json_({ ok: true, request_id: requestId });
  } catch (error) {
    return json_({ ok: false, error: String(error.message || error) });
  }
}

function doGet(event) {
  const callback = safeCallback_(event && event.parameter && event.parameter.callback);
  let result;
  try {
    const action = String((event && event.parameter && event.parameter.action) || "health");
    if (action === "health") {
      result = { ok: true, service: "balance-codex-drive-queue" };
    } else if (action === "status") {
      result = getStatus_(validateRequestId_(event.parameter.request_id));
    } else {
      throw new Error("Unknown action");
    }
  } catch (error) {
    result = { ok: false, status: "failed", error: String(error.message || error) };
  }
  return callback ? javascript_(callback, result) : json_(result);
}

function getStatus_(requestId) {
  const folder = DriveApp.getFolderById(QUEUE_FOLDER_ID);
  const answers = folder.getFilesByName(ANSWER_PREFIX + requestId + ".json");
  if (answers.hasNext()) {
    const answer = JSON.parse(answers.next().getBlob().getDataAsString("UTF-8"));
    return {
      ok: true,
      request_id: requestId,
      status: answer.status === "failed" ? "failed" : "completed",
      answer: String(answer.answer || ""),
      error: String(answer.error || ""),
      completed_at: answer.completed_at || null
    };
  }
  const questions = folder.getFilesByName(QUESTION_PREFIX + requestId + ".json");
  if (!questions.hasNext()) return { ok: true, request_id: requestId, status: "pending" };
  const question = JSON.parse(questions.next().getBlob().getDataAsString("UTF-8"));
  return { ok: true, request_id: requestId, status: question.status || "pending" };
}

function cleanupOldQueueFiles() {
  const folder = DriveApp.getFolderById(QUEUE_FOLDER_ID);
  const cutoff = Date.now() - 14 * 24 * 60 * 60 * 1000;
  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();
    if (file.getDateCreated().getTime() < cutoff) file.setTrashed(true);
  }
}

function parsePayload_(event) {
  const raw = event && event.parameter && event.parameter.payload
    ? event.parameter.payload
    : event && event.postData && event.postData.contents;
  if (!raw) throw new Error("Empty request");
  return JSON.parse(raw);
}

function validateRequestId_(value) {
  const id = String(value || "").trim();
  if (!ID_PATTERN.test(id)) throw new Error("Invalid request ID");
  return id;
}

function safeCallback_(value) {
  const callback = String(value || "");
  return /^[A-Za-z_$][0-9A-Za-z_$.]{0,80}$/.test(callback) ? callback : "";
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function javascript_(callback, value) {
  return ContentService.createTextOutput(callback + "(" + JSON.stringify(value) + ");")
    .setMimeType(ContentService.MimeType.JAVASCRIPT);
}

