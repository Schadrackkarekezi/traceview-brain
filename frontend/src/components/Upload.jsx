import { useEffect, useState } from "react";
import { runQuery, uploadAccountNote, uploadEnablementContent } from "../api";

const CATEGORIES = ["battlecard", "sales_play", "objection_handling", "faq"];
const ROLES = ["AE", "SE", "CSM"];

export default function Upload() {
  const [kind, setKind] = useState("note"); // "note" | "enablement"
  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState("");
  const [authorRole, setAuthorRole] = useState("CSM");
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    runQuery("SELECT account_id, name FROM accounts ORDER BY name")
      .then((data) => setAccounts(data.rows.map(([id, name]) => ({ id, name }))))
      .catch(() => {}); // the account dropdown just stays empty; the form still works with a typed ID
  }, []);

  const reset = () => {
    setContent("");
    setTitle("");
  };

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data =
        kind === "note"
          ? await uploadAccountNote(Number(accountId), content, authorRole)
          : await uploadEnablementContent(title, category, content);
      setResult(data);
      reset();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit =
    content.trim().length > 0 && (kind === "note" ? accountId !== "" : title.trim().length > 0);

  return (
    <div className="upload-view">
      <p className="upload-intro">
        Add a new account note or piece of enablement content - it's chunked and embedded
        immediately, so it's retrievable by the agent right after you submit. Account notes are
        scoped to the account you pick and can never surface in a question about a different one;
        enablement content is global.
      </p>

      <div className="upload-kind-toggle">
        <button className={kind === "note" ? "active" : ""} onClick={() => setKind("note")}>
          Account Note
        </button>
        <button className={kind === "enablement" ? "active" : ""} onClick={() => setKind("enablement")}>
          Enablement Content
        </button>
      </div>

      <form className="upload-form" onSubmit={submit}>
        {kind === "note" ? (
          <>
            <label className="upload-field">
              <span>Account</span>
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
                <option value="" disabled>
                  Select an account…
                </option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="upload-field">
              <span>Author role</span>
              <select value={authorRole} onChange={(e) => setAuthorRole(e.target.value)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : (
          <>
            <label className="upload-field">
              <span>Title</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Objection: Contract length" />
            </label>
            <label className="upload-field">
              <span>Category</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}

        <label className="upload-field upload-field-content">
          <span>Content</span>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={8}
            placeholder={
              kind === "note"
                ? "QBR note - what happened, why, what's the plan…"
                : "The actual battlecard / play / FAQ text…"
            }
          />
        </label>

        <button className="run-btn upload-submit" type="submit" disabled={!canSubmit || submitting}>
          {submitting ? <span className="spinner" /> : "Upload"}
        </button>
      </form>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <div className="upload-success">
          Uploaded - split into {result.chunks_created} chunk{result.chunks_created > 1 ? "s" : ""}, all{" "}
          {result.chunks_embedded} embedded and retrievable now.
          {result.hydra_indexed === true && " Also sent to HydraDB, where it joins the knowledge graph once indexed."}
          {result.hydra_indexed === false && " HydraDB couldn't be reached, so it's searchable through pgvector only for now."}{" "}
          Try asking about it in Ask AI.
        </div>
      )}
    </div>
  );
}
