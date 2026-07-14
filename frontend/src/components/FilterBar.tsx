import { useEffect, useState } from "react";
import { api, Filters, UNASSIGNED } from "../api";
import TypeFilterSelect from "./TypeFilterSelect";

interface Props {
  datasetId: number;
  filters: Filters;
  onChange: (f: Filters) => void;
}

export default function FilterBar({ datasetId, filters, onChange }: Props) {
  const [types, setTypes] = useState<{ file_type: string; count: number }[]>([]);
  const [jiraVals, setJiraVals] = useState<string[]>([]);
  const [assigneeVals, setAssigneeVals] = useState<string[]>([]);

  useEffect(() => {
    api.fileTypes(datasetId).then(setTypes).catch(() => setTypes([]));
    api.distinctValues(datasetId, "jira_ticket").then((r) => setJiraVals(r.values)).catch(() => {});
    api.distinctValues(datasetId, "assignee").then((r) => setAssigneeVals(r.values)).catch(() => {});
  }, [datasetId, filters]);

  return (
    <div className="filterbar">
      <TypeFilterSelect
        types={types}
        selected={filters.types}
        onChange={(next) => onChange({ ...filters, types: next })}
      />

      <div className="filter-group">
        <label>Last accessed after</label>
        <input
          type="date"
          value={filters.accessed_after || ""}
          onChange={(e) =>
            onChange({ ...filters, accessed_after: e.target.value || undefined })
          }
        />
      </div>
      <div className="filter-group">
        <label>Last accessed before</label>
        <input
          type="date"
          value={filters.accessed_before || ""}
          onChange={(e) =>
            onChange({ ...filters, accessed_before: e.target.value || undefined })
          }
        />
      </div>

      <div className="filter-group">
        <label>No Transfer</label>
        <select
          value={filters.no_transfer || ""}
          onChange={(e) =>
            onChange({
              ...filters,
              no_transfer: (e.target.value || undefined) as "yes" | "no" | undefined,
            })
          }
        >
          <option value="">Any</option>
          <option value="no">Hide marked</option>
          <option value="yes">Only marked</option>
        </select>
      </div>
      <div className="filter-group">
        <label>Processed</label>
        <select
          value={filters.processed || ""}
          onChange={(e) =>
            onChange({
              ...filters,
              processed: (e.target.value || undefined) as "yes" | "no" | undefined,
            })
          }
        >
          <option value="">Any</option>
          <option value="no">Hide processed</option>
          <option value="yes">Only processed</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Assignee</label>
        <select
          value={filters.assignee || ""}
          onChange={(e) =>
            onChange({ ...filters, assignee: e.target.value || undefined })
          }
        >
          <option value="">Any</option>
          <option value={UNASSIGNED}>Unassigned</option>
          {assigneeVals.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>
      <div className="filter-group">
        <label>JIRA</label>
        <select
          value={filters.jira || ""}
          onChange={(e) =>
            onChange({ ...filters, jira: e.target.value || undefined })
          }
        >
          <option value="">Any</option>
          <option value={UNASSIGNED}>No ticket</option>
          {jiraVals.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>

      {((filters.types && filters.types.length > 0) ||
        filters.accessed_after ||
        filters.accessed_before ||
        filters.no_transfer ||
        filters.processed ||
        filters.jira ||
        filters.assignee) && (
        <div className="filter-group">
          <label>&nbsp;</label>
          <button
            onClick={() => onChange({})}
            title="Clear all filters"
          >
            Reset filters
          </button>
        </div>
      )}
    </div>
  );
}
