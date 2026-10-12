const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

// Only sent if VITE_API_KEY is set at build time - auth is opt-in server-side
// too (backend/auth.py), so local dev with no key configured needs no header.
const API_KEY = import.meta.env.VITE_API_KEY;

function headers(extra = {}) {
  return API_KEY ? { ...extra, "X-API-Key": API_KEY } : extra;
}

// Every non-streaming call below is fetch + parse JSON + throw with the
// server's own detail message (falling back to a generic one) on failure
// - one helper instead of six copies of that pattern.
async function apiFetch(path, options, label) {
  const res = await fetch(`${BASE_URL}${path}`, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `${label} (${res.status})`);
  return data;
}

export async function getSchema() {
  return apiFetch("/schema", undefined, "Failed to load schema");
}

export async function getGraph() {
  return apiFetch("/graph", undefined, "Failed to load graph");
}

export async function runQuery(sql) {
  return apiFetch(
    "/query",
    { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ sql }) },
    "Query failed"
  );
}

// Only "unstructured"/"hybrid" answers ever send "start"/"delta" events
// (that's the one call in the pipeline that writes free-form prose instead
// of strict JSON, so it's the only one that can stream meaningfully);
// "sql"/"chat" turns go straight to onComplete. A backend "error" event is
// thrown here rather than passed to a callback, so callers can handle it
// with the same try/catch they'd use for a network failure.
//
// userId is who's asking - what the backend scopes long-term memory by.
// Left out of the body entirely when blank, which the backend treats as
// "no memory for this turn" rather than as a user named "".
export async function askQuestionStream(question, conversationId, { userId, onStart, onDelta, onSynthError, onComplete }) {
  const res = await fetch(`${BASE_URL}/ask/stream`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question, conversation_id: conversationId, user_id: userId || undefined }),
  });

  if (!res.ok || !res.body) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* body wasn't JSON - fall through to the generic message below */
    }
    throw new Error(detail || `Ask failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = rawEvent.split("\n").find((l) => l.startsWith("data: "));
      if (!dataLine) continue;
      const event = JSON.parse(dataLine.slice(6));

      if (event.type === "start") onStart?.(event.data);
      else if (event.type === "delta") onDelta?.(event.text);
      else if (event.type === "synth_error") onSynthError?.(event.text);
      else if (event.type === "complete") onComplete?.(event.data);
      else if (event.type === "error") throw new Error(event.message);
    }
  }
}

export async function clearConversation(conversationId) {
  await fetch(`${BASE_URL}/conversations/${conversationId}`, {
    method: "DELETE",
    headers: headers(),
  });
}

export async function getBrainStatus() {
  return apiFetch("/brain/status", undefined, "Failed to load brain status");
}

export async function getMemories(userId) {
  return apiFetch(`/memory?user_id=${encodeURIComponent(userId)}`, { headers: headers() }, "Failed to load memories");
}

export async function addMemory(userId, text) {
  return apiFetch(
    "/memory",
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ user_id: userId, text }),
    },
    "Failed to save memory"
  );
}

export async function deleteMemory(userId, memoryId) {
  return apiFetch(
    `/memory/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE", headers: headers() },
    "Failed to forget memory"
  );
}

export async function sendFeedback(hydraRequestId, rating) {
  return apiFetch(
    "/feedback",
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ hydra_request_id: hydraRequestId, rating }),
    },
    "Failed to send feedback"
  );
}

export async function getLogs(limit = 50) {
  return apiFetch(`/logs?limit=${limit}`, { headers: headers() }, "Failed to load logs");
}

export async function uploadAccountNote(accountId, content, authorRole) {
  return apiFetch(
    "/upload/account-note",
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ account_id: accountId, content, author_role: authorRole }),
    },
    "Upload failed"
  );
}

export async function uploadEnablementContent(title, category, content) {
  return apiFetch(
    "/upload/enablement-content",
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ title, category, content }),
    },
    "Upload failed"
  );
}
