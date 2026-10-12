import { useCallback, useEffect, useState } from "react";
import { getMemories, addMemory, deleteMemory } from "../api";
import { CloseIcon } from "./Icons";

const ORIGIN_LABELS = {
  conversation: "Learned from a conversation",
  explicit: "Told directly",
};

// Long-term memory is asynchronous on HydraDB's side - a memory is
// accepted immediately but only becomes listable/recallable once it's
// indexed - so anything just added is shown from local state as
// "indexing" until a refresh finds it for real, rather than appearing to
// have been dropped.
export default function Memory({ active, userId, onUserChange, brain, onRefreshBrain }) {
  const [draftName, setDraftName] = useState(userId);
  const [memories, setMemories] = useState([]);
  const [pending, setPending] = useState([]);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const enabled = brain?.enabled;

  const load = useCallback(async () => {
    if (!enabled || !userId) {
      setMemories([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getMemories(userId);
      setMemories(data.memories);
      setPending((prev) => prev.filter((p) => !data.memories.some((m) => m.id === p.id)));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [enabled, userId]);

  // Reloads on every visit to this page, not just on mount - Ask AI writes
  // memories in the background, so what was true the last time this page
  // was open usually isn't any more.
  useEffect(() => {
    if (active) {
      load();
      onRefreshBrain?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, load]);

  useEffect(() => {
    setDraftName(userId);
    setPending([]);
  }, [userId]);

  const saveName = (e) => {
    e.preventDefault();
    onUserChange(draftName.trim());
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!text.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const result = await addMemory(userId, text.trim());
      setPending((prev) => [{ id: result.id, text: text.trim(), origin: "explicit" }, ...prev]);
      setText("");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const forget = async (id) => {
    setError(null);
    try {
      await deleteMemory(userId, id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      setPending((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      setError(err.message);
    }
  };

  if (brain && !enabled) {
    return (
      <div className="memory-view">
        <p className="upload-intro">
          Long-term memory runs on HydraDB, which isn't configured on this backend - answers are still
          grounded in the knowledge base through pgvector, but nothing is remembered between conversations.
          Set <code>HYDRA_DB_API_KEY</code> in <code>backend/.env</code>, run{" "}
          <code>python -m scripts.seed_hydra</code>, and restart the backend to turn it on.
        </p>
      </div>
    );
  }

  const knowledgeRows = brain?.knowledge_collection?.row_count;
  const memoryRows = brain?.memory_collection?.row_count;
  const all = [...pending.map((p) => ({ ...p, indexing: true })), ...memories];

  return (
    <div className="memory-view">
      <p className="upload-intro">
        What Traceview remembers about whoever is asking - who they are, what they cover, how they like
        answers. It's learned from conversations, kept separately for each person, and recalled whenever
        it's relevant to a question, so "my accounts" means something different depending on who asks.
      </p>

      {brain && (
        <div className="memory-stats">
          <span>
            Database <strong>{brain.database}</strong>
          </span>
          {knowledgeRows != null && (
            <span>
              <strong>{knowledgeRows.toLocaleString()}</strong> knowledge chunks
            </span>
          )}
          {memoryRows != null && (
            <span>
              <strong>{memoryRows.toLocaleString()}</strong> memories across all users
            </span>
          )}
          <span>
            Retrieval mode <strong>{brain.account_query_mode}</strong> for accounts, <strong>{brain.query_mode}</strong>{" "}
            for playbooks
          </span>
        </div>
      )}

      <form className="upload-form memory-identity" onSubmit={saveName}>
        <label className="upload-field">
          Who's asking? Just your name - what you want remembered goes in the box below.
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder="e.g. Derek Reyes"
            maxLength={60}
          />
        </label>
        <button className="run-btn upload-submit" type="submit" disabled={draftName.trim() === userId}>
          {userId ? "Switch" : "Start remembering"}
        </button>
      </form>

      {userId && (
        <>
          <form className="upload-form memory-add" onSubmit={submit}>
            <label className="upload-field">
              Tell it something to remember about {userId}
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={2}
                placeholder="e.g. I'm the CSM for Sinclair Care Partners and I want answers in three bullets or fewer."
              />
            </label>
            <button className="run-btn upload-submit" type="submit" disabled={!text.trim() || saving}>
              {saving ? <span className="spinner" /> : "Remember"}
            </button>
          </form>

          {error && <div className="error-banner">{error}</div>}

          <div className="memory-list-head">
            <span>
              {loading ? "Loading…" : `${all.length} memor${all.length === 1 ? "y" : "ies"} about ${userId}`}
            </span>
            <button className="memory-refresh" onClick={load} disabled={loading}>
              Refresh
            </button>
          </div>

          {all.length === 0 && !loading && (
            <p className="memory-empty">
              Nothing yet. Tell it something above, or just mention it in Ask AI - "I cover the healthcare
              accounts" - and it will be here next time.
            </p>
          )}

          <ul className="memory-list">
            {all.map((m) => (
              <li key={m.id || m.text} className={`memory-item${m.indexing ? " indexing" : ""}`}>
                <div className="memory-item-body">
                  <div className="memory-item-text">{m.text || "(no text returned)"}</div>
                  <div className="memory-item-meta">
                    {m.indexing ? "Indexing - recallable in a moment" : ORIGIN_LABELS[m.origin] || "Memory"}
                    {m.timestamp ? ` · ${new Date(m.timestamp).toLocaleString()}` : ""}
                  </div>
                </div>
                {m.id && (
                  <button className="memory-forget" onClick={() => forget(m.id)} aria-label="Forget this memory" title="Forget">
                    <CloseIcon width={11} height={11} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
