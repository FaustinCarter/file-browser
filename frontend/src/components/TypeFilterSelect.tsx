import { useEffect, useRef, useState } from "react";

// Searchable multi-select for file types, shared by the tree FilterBar and the
// grid view so both offer the same "filter by several types at once" control.
export default function TypeFilterSelect({
  types,
  selected,
  onChange,
}: {
  types: { file_type: string; count: number }[];
  selected: string[] | undefined;
  onChange: (next: string[] | undefined) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const sel = new Set(selected || []);
  const toggle = (t: string) => {
    const next = new Set(sel);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange(next.size ? Array.from(next) : undefined);
  };

  const filtered = types.filter((t) =>
    t.file_type?.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="filter-group typefilter" ref={ref}>
      <label>File type filter ({types.length} types)</label>
      <button onClick={() => setOpen((o) => !o)}>
        {sel.size ? `${sel.size} type(s) selected` : "All file types"} ▾
      </button>
      {open && (
        <div className="typefilter-dropdown">
          <input
            autoFocus
            placeholder="Search types…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: "100%", marginBottom: 6 }}
          />
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <button onClick={() => onChange(undefined)}>Clear</button>
            <span className="muted" style={{ alignSelf: "center" }}>
              {sel.size} selected
            </span>
          </div>
          {filtered.map((t) => (
            <div
              key={t.file_type}
              className="typefilter-row"
              onClick={() => toggle(t.file_type)}
            >
              <input type="checkbox" readOnly checked={sel.has(t.file_type)} />
              <span>{t.file_type}</span>
              <span className="cnt">{t.count.toLocaleString()}</span>
            </div>
          ))}
          {filtered.length === 0 && <div className="muted">No matches</div>}
        </div>
      )}
    </div>
  );
}
