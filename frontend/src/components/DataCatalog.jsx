import { useEffect, useMemo, useState } from "react";
import { getSchema } from "../api";
import { SearchIcon } from "./Icons";

export default function DataCatalog({ onPreview }) {
  const [tables, setTables] = useState([]);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    getSchema()
      .then((data) => setTables(data.tables))
      .catch((err) => setError(err.message));
  }, []);

  const filtered = useMemo(
    () =>
      tables.filter(
        (t) =>
          t.name.toLowerCase().includes(filter.toLowerCase()) ||
          t.columns.some((c) => c.name.toLowerCase().includes(filter.toLowerCase()))
      ),
    [tables, filter]
  );

  const dimensions = filtered.filter((t) => t.kind === "dimension");
  const facts = filtered.filter((t) => t.kind === "fact");
  const other = filtered.filter((t) => t.kind !== "dimension" && t.kind !== "fact");

  if (error) return <div className="error-banner">{error}</div>;

  const renderCard = (table) => (
    <div className="catalog-card" key={table.name}>
      <div className="catalog-card-header">
        <span className={`catalog-kind-badge ${table.kind || ""}`}>{table.kind || "table"}</span>
        <button className="catalog-preview-btn" onClick={() => onPreview(table.name)}>
          Preview data
        </button>
      </div>
      <h3>{table.name}</h3>
      <ul className="catalog-columns">
        {table.columns.map((c) => (
          <li key={c.name}>
            <span className="catalog-col-name">{c.name}</span>
            <span className="catalog-col-type">{c.type}</span>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <div className="data-catalog">
      <div className="catalog-search-row">
        <SearchIcon className="catalog-search-icon" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search tables or columns…"
        />
      </div>

      {dimensions.length > 0 && (
        <>
          <div className="catalog-section-label">Dimensions</div>
          <div className="catalog-grid">{dimensions.map(renderCard)}</div>
        </>
      )}

      {facts.length > 0 && (
        <>
          <div className="catalog-section-label">Facts</div>
          <div className="catalog-grid">{facts.map(renderCard)}</div>
        </>
      )}

      {other.length > 0 && (
        <>
          <div className="catalog-section-label">Other</div>
          <div className="catalog-grid">{other.map(renderCard)}</div>
        </>
      )}

      {tables.length > 0 && filtered.length === 0 && (
        <div className="schema-empty">No matching tables or columns</div>
      )}
    </div>
  );
}
