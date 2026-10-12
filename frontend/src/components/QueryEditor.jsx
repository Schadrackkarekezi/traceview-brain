import { useEffect } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql as sqlLang } from "@codemirror/lang-sql";
import { useTheme } from "../useTheme";
import { PlayIcon, TerminalIcon } from "./Icons";

export default function QueryEditor({ sql, setSql, onRun, loading }) {
  const [, , resolvedTheme] = useTheme();

  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onRun();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onRun]);

  return (
    <div className="query-editor">
      <p className="query-editor-intro">
        Write raw SQL directly against the schema - read-only, capped at 500 rows, 5s timeout.
      </p>

      <div className="code-frame">
        <div className="code-frame-toolbar">
          <TerminalIcon width={14} height={14} />
          <span>SQL</span>
        </div>
        <CodeMirror
          value={sql}
          height="140px"
          theme={resolvedTheme}
          extensions={[sqlLang()]}
          basicSetup={{ foldGutter: false }}
          onChange={setSql}
        />
      </div>
      <button className="run-btn" onClick={onRun} disabled={loading}>
        {loading ? <span className="spinner" /> : <PlayIcon width={13} height={13} />}
        Run
        <span className="shortcut">⌘/Ctrl + Enter</span>
      </button>
    </div>
  );
}
