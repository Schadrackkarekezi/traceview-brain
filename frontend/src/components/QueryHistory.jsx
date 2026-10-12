import { useEffect, useRef, useState } from "react";
import { ClockIcon } from "./Icons";

export default function QueryHistory({ history, onSelect, onClear }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div className="query-history" ref={ref}>
      <button className="history-toggle" onClick={() => setOpen((o) => !o)}>
        <ClockIcon width={13} height={13} />
        History{history.length > 0 && <span className="badge">{history.length}</span>}
      </button>
      {open && (
        <div className="history-panel">
          {history.length === 0 ? (
            <div className="history-empty">No queries yet</div>
          ) : (
            <>
              <ul>
                {history.map((item, i) => (
                  <li key={i}>
                    <button
                      onClick={() => {
                        onSelect(item.sql);
                        setOpen(false);
                      }}
                    >
                      <code>{item.sql}</code>
                      <span className="history-time">{item.time}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <button className="history-clear" onClick={onClear}>
                Clear history
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
