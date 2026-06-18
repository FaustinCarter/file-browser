import { useEffect, useRef, useState } from "react";
import { api, Dataset, Filters, NodeOut } from "./api";
import FilterBar from "./components/FilterBar";
import TreeView from "./components/TreeView";
import DetailPanel from "./components/DetailPanel";
import GridView from "./components/GridView";

export default function App() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [tab, setTab] = useState<"tree" | "grid">("tree");
  const [filters, setFilters] = useState<Filters>({});
  const [selected, setSelected] = useState<NodeOut | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [refreshKey, setRefreshKey] = useState(0);
  const [userName, setUserName] = useState(localStorage.getItem("fb_user") || "");
  const [toastMsg, setToast] = useState<{ msg: string; err?: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const toast = (msg: string, err = false) => {
    setToast({ msg, err });
    setTimeout(() => setToast(null), 2200);
  };

  const refreshDatasets = () =>
    api.listDatasets().then((d) => {
      setDatasets(d);
      setCurrent((c) => c ?? (d[0]?.id ?? null));
    });

  useEffect(() => {
    refreshDatasets();
  }, []);

  // Reset selection/filters/expansion when switching datasets.
  useEffect(() => {
    setSelected(null);
    setFilters({});
    setExpanded(new Set());
  }, [current]);

  async function onUpload(file: File) {
    try {
      toast("Importing…");
      const ds = await api.uploadDataset(file, "");
      await refreshDatasets();
      setCurrent(ds.id);
      toast(`Imported ${ds.row_count.toLocaleString()} rows`);
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Delete this dataset and all its annotations?")) return;
    await api.deleteDataset(id);
    setCurrent(null);
    await refreshDatasets();
    setSelected(null);
  }

  if (!userName) {
    return <NamePrompt onSet={(n) => {
      localStorage.setItem("fb_user", n);
      setUserName(n);
    }} />;
  }

  return (
    <div className="app">
      <div className="topbar">
        <h1>🗂️ File Browser &amp; Migration Tagger</h1>
        <select
          value={current ?? ""}
          onChange={(e) => setCurrent(e.target.value ? Number(e.target.value) : null)}
        >
          {datasets.length === 0 && <option value="">No datasets — upload one →</option>}
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.row_count.toLocaleString()} rows)
            </option>
          ))}
        </select>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
        <button className="primary" onClick={() => fileRef.current?.click()}>
          + Upload CSV
        </button>
        {current && (
          <button className="danger" onClick={() => onDelete(current)}>
            Delete dataset
          </button>
        )}

        <div className="tabs" style={{ marginLeft: 12 }}>
          <button
            className={tab === "tree" ? "active" : ""}
            onClick={() => setTab("tree")}
          >
            Tree explorer
          </button>
          <button
            className={tab === "grid" ? "active" : ""}
            onClick={() => setTab("grid")}
          >
            Grid / bulk edit
          </button>
        </div>

        <div className="spacer" />
        <span className="tag">
          user: <b>{userName}</b>{" "}
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              localStorage.removeItem("fb_user");
              setUserName("");
            }}
          >
            change
          </a>
        </span>
      </div>

      {current == null ? (
        <div className="empty">
          No dataset selected. Upload a CSV export to get started.
        </div>
      ) : tab === "tree" ? (
        <div className="layout">
          <div className="left">
            <FilterBar datasetId={current} filters={filters} onChange={setFilters} />
            <TreeView
              datasetId={current}
              filters={filters}
              selectedId={selected?.id ?? null}
              onSelect={setSelected}
              refreshKey={refreshKey}
              expanded={expanded}
              setExpanded={setExpanded}
            />
          </div>
          <div className="right">
            {selected ? (
              <DetailPanel
                key={selected.id}
                nodeId={selected.id}
                userName={userName}
                filters={filters}
                onMutated={() => setRefreshKey((k) => k + 1)}
                toast={toast}
              />
            ) : (
              <div className="detail muted">
                Select a file or folder to view details and edit tags.
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="left" style={{ flex: 1, borderRight: "none" }}>
          <GridView datasetId={current} userName={userName} toast={toast} />
        </div>
      )}

      {toastMsg && (
        <div className={`toast${toastMsg.err ? " err" : ""}`}>{toastMsg.msg}</div>
      )}
    </div>
  );
}

function NamePrompt({ onSet }: { onSet: (n: string) => void }) {
  const [name, setName] = useState("");
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Welcome 👋</h2>
        <p className="muted">
          Enter a display name. It will be attached to tags and edits you make so
          your team can see who changed what.
        </p>
        <input
          autoFocus
          style={{ width: "100%", marginBottom: 12 }}
          placeholder="e.g. Faustin"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && onSet(name.trim())}
        />
        <button className="primary" disabled={!name.trim()} onClick={() => onSet(name.trim())}>
          Continue
        </button>
      </div>
    </div>
  );
}
