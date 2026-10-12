import { useMemo, useState } from "react";
import { ClockIcon, DatabaseIcon } from "./Icons";

export default function ResultsTable({ result, loading }) {
  const [sort, setSort] = useState({ index: null, dir: 1 });

  const sortedRows = useMemo(() => {
    if (!result) return [];
    if (sort.index === null) return result.rows;
    const idx = sort.index;
    return [...result.rows].sort((a, b) => {
      const av = a[idx];
      const bv = b[idx];
      if (av === null) return 1;
      if (bv === null) return -1;
      if (av === bv) return 0;
      return (av > bv ? 1 : -1) * sort.dir;
    });
  }, [result, sort]);

  const toggleSort = (i) => {
    setSort((prev) =>
      prev.index === i ? { index: i, dir: prev.dir * -1 } : { index: i, dir: 1 }
    );
  };

  if (loading) {
    return (
      <div className="results-skeleton">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton-row" />
        ))}
      </div>
    );
  }

  if (!result) {
    return (
      <div className="results-empty">
        <DatabaseIcon width={20} height={20} />
        <p>Run a query to see results here.</p>
      </div>
    );
  }

  const { columns, row_count, execution_time_ms, truncated } = result;

  return (
    <div className="results-table">
      <div className="results-meta">
        <span className="results-meta-item">
          <DatabaseIcon width={13} height={13} />
          {row_count} row{row_count === 1 ? "" : "s"}
        </span>
        <span className="results-meta-item">
          <ClockIcon width={13} height={13} />
          {execution_time_ms} ms
        </span>
        {truncated && (
          <span className="truncated-badge">
            truncated at {row_count} rows - refine your query to see more
          </span>
        )}
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((col, i) => (
                <th key={col} onClick={() => toggleSort(i)}>
                  {col}
                  {sort.index === i && (
                    <span className="sort-arrow">{sort.dir === 1 ? "▲" : "▼"}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) =>
                  cell === null ? (
                    <td key={j}>
                      <span className="null-cell">NULL</span>
                    </td>
                  ) : (
                    <td key={j}>{String(cell)}</td>
                  )
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
