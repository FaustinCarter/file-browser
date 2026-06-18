import { useCallbackRef } from "../hooks";
import { useEffect, useRef, useState } from "react";
import { api, FlagState, Filters, fmtBytes, folderFlagState, NodeOut } from "../api";

function flagState(node: NodeOut, field: "no_transfer" | "processed"): FlagState {
  if (node.is_dir) {
    const marked =
      field === "no_transfer" ? node.no_transfer_marked : node.processed_marked;
    return folderFlagState(marked, node.total_files);
  }
  return node.effective?.[field] ? "all" : "none";
}

type ExpandedSetter = (updater: (prev: Set<number>) => Set<number>) => void;

interface Props {
  datasetId: number;
  filters: Filters;
  selectedId: number | null;
  onSelect: (n: NodeOut) => void;
  refreshKey: number;
  // Expansion state lives in the parent so it survives filter changes / tab
  // switches (the tree refetches data but keeps which folders are open).
  expanded: Set<number>;
  setExpanded: ExpandedSetter;
}

export default function TreeView({
  datasetId,
  filters,
  selectedId,
  onSelect,
  refreshKey,
  expanded,
  setExpanded,
}: Props) {
  const [roots, setRoots] = useState<NodeOut[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const seededRef = useRef<number | null>(null);

  // Show the loading placeholder only when the dataset itself changes; filter
  // changes refetch in place so expansion state and scroll position are kept.
  useEffect(() => {
    setRoots(null);
    seededRef.current = null;
  }, [datasetId]);

  useEffect(() => {
    let cancelled = false;
    setRefreshing(true);
    api
      .children(datasetId, null, filters)
      .then((r) => {
        if (cancelled) return;
        setRoots(r.children);
        // Expand the top-level folders the first time we load a dataset.
        if (seededRef.current !== datasetId) {
          seededRef.current = datasetId;
          const rootIds = r.children.filter((c) => c.is_dir).map((c) => c.id);
          if (rootIds.length) {
            setExpanded((prev) => {
              const next = new Set(prev);
              rootIds.forEach((id) => next.add(id));
              return next;
            });
          }
        }
      })
      .catch(() => {
        if (!cancelled) setRoots([]);
      })
      .finally(() => {
        if (!cancelled) setRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, filters, refreshKey, setExpanded]);

  if (roots === null) return <div className="empty">Loading…</div>;
  if (roots.length === 0) return <div className="empty">No data in this dataset.</div>;

  return (
    <div className="tree">
      {refreshing && (
        <div className="muted" style={{ padding: "1px 8px", fontSize: 11 }}>
          updating…
        </div>
      )}
      {roots.map((n) => (
        <TreeNode
          key={n.id}
          node={n}
          datasetId={datasetId}
          filters={filters}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
          refreshKey={refreshKey}
          expanded={expanded}
          setExpanded={setExpanded}
        />
      ))}
    </div>
  );
}

function TreeNode({
  node,
  datasetId,
  filters,
  depth,
  selectedId,
  onSelect,
  refreshKey,
  expanded,
  setExpanded,
}: {
  node: NodeOut;
  datasetId: number;
  filters: Filters;
  depth: number;
  selectedId: number | null;
  onSelect: (n: NodeOut) => void;
  refreshKey: number;
  expanded: Set<number>;
  setExpanded: ExpandedSetter;
}) {
  const isExpanded = node.is_dir && expanded.has(node.id);
  const [children, setChildren] = useState<NodeOut[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallbackRef(() => {
    if (!node.is_dir) return;
    setLoading(true);
    api
      .children(datasetId, node.id, filters)
      .then((r) => setChildren(r.children))
      .finally(() => setLoading(false));
  });

  // (Re)load children whenever this node is open and the filters/refresh change.
  useEffect(() => {
    if (isExpanded) load();
  }, [isExpanded, filters, refreshKey]);

  const toggle = () => {
    if (!node.is_dir) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  };

  const isSel = selectedId === node.id;
  const eff = node.effective;

  return (
    <div>
      <div
        className={`tree-row${isSel ? " selected" : ""}`}
        style={{ paddingLeft: 8 + depth * 16 }}
        onClick={() => onSelect(node)}
      >
        <span
          className="twisty"
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
        >
          {node.is_dir ? (isExpanded ? "▾" : "▸") : ""}
        </span>
        <span className="icon">{node.is_dir ? "📁" : "📄"}</span>
        <span className="nm">{node.name}</span>
        {(() => {
          const nt = flagState(node, "no_transfer");
          const pr = flagState(node, "processed");
          return (
            <>
              {nt === "all" && <span className="badge keep">NO XFER</span>}
              {nt === "some" && (
                <span className="badge warn" title="Mixed: some files not marked">
                  NO XFER⚠
                </span>
              )}
              {pr === "all" && <span className="badge proc">PROC</span>}
              {pr === "some" && (
                <span className="badge warn" title="Mixed: some files not processed">
                  PROC⚠
                </span>
              )}
            </>
          );
        })()}
        {eff?.jira_ticket && <span className="badge">{eff.jira_ticket}</span>}
        <span className="meta">
          {node.is_dir ? (
            <span title="files under here (respecting filters)">
              {(node.filtered_file_count ?? 0).toLocaleString()} files
            </span>
          ) : (
            <span>{node.file_type}</span>
          )}
          <span>
            {node.is_dir
              ? fmtBytes(node.filtered_total_size)
              : fmtBytes(node.size_bytes)}
          </span>
          <span className="muted">{node.last_accessed || ""}</span>
        </span>
      </div>
      {isExpanded && (
        <div>
          {loading && children === null && (
            <div className="muted" style={{ paddingLeft: 8 + (depth + 1) * 16 }}>
              loading…
            </div>
          )}
          {children?.map((c) => (
            <TreeNode
              key={c.id}
              node={c}
              datasetId={datasetId}
              filters={filters}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              refreshKey={refreshKey}
              expanded={expanded}
              setExpanded={setExpanded}
            />
          ))}
          {children && children.length === 0 && !loading && (
            <div
              className="muted"
              style={{ paddingLeft: 8 + (depth + 1) * 16, fontSize: 11 }}
            >
              (empty)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
