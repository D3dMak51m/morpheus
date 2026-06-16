import React, { useMemo, useState } from 'react';
import { ChevronUp, ChevronDown, Search } from 'lucide-react';
import './DataTable.css';

export interface Column<T> {
  key: string;
  header: string;
  /** Custom cell renderer; defaults to String(row[key]). */
  render?: (row: T) => React.ReactNode;
  /** Value used for sorting; defaults to row[key]. */
  sortValue?: (row: T) => string | number | null | undefined;
  sortable?: boolean;          // default true
  align?: 'left' | 'right' | 'center';
  width?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  loading?: boolean;
  /** Text built from a row to match against the search box. */
  searchText?: (row: T) => string;
  searchPlaceholder?: string;
  emptyText?: string;
  pageSize?: number;           // 0 = no pagination
  /** Extra controls (filters) rendered in the toolbar next to search. */
  toolbar?: React.ReactNode;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  columns, rows, rowKey, loading, searchText, searchPlaceholder = 'Поиск…',
  emptyText = 'Ничего не найдено.', pageSize = 25, toolbar, onRowClick,
}: DataTableProps<T>) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(0);

  const colByKey = useMemo(() => Object.fromEntries(columns.map(c => [c.key, c])), [columns]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !searchText) return rows;
    return rows.filter(r => searchText(r).toLowerCase().includes(q));
  }, [rows, query, searchText]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const col = colByKey[sortKey];
    if (!col) return filtered;
    const val = (r: T) => {
      const v = col.sortValue ? col.sortValue(r) : (r as Record<string, unknown>)[sortKey];
      return v == null ? '' : v;
    };
    const arr = [...filtered].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (typeof va === 'number' && typeof vb === 'number') return va - vb;
      return String(va).localeCompare(String(vb), 'ru');
    });
    return sortDir === 'asc' ? arr : arr.reverse();
  }, [filtered, sortKey, sortDir, colByKey]);

  const pageCount = pageSize > 0 ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const safePage = Math.min(page, pageCount - 1);
  const visible = pageSize > 0 ? sorted.slice(safePage * pageSize, safePage * pageSize + pageSize) : sorted;

  const toggleSort = (key: string) => {
    const col = colByKey[key];
    if (col && col.sortable === false) return;
    if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('asc'); }
  };

  return (
    <div className="dt-wrap">
      <div className="dt-toolbar">
        {searchText && (
          <div className="dt-search">
            <Search size={14} />
            <input value={query} placeholder={searchPlaceholder}
              onChange={e => { setQuery(e.target.value); setPage(0); }} />
          </div>
        )}
        {toolbar}
        <span className="dt-count">{sorted.length} запис.</span>
      </div>

      <div className="dt-scroll">
        <table className="dt-table">
          <thead>
            <tr>
              {columns.map(c => {
                const active = sortKey === c.key;
                const canSort = c.sortable !== false;
                return (
                  <th key={c.key} style={{ width: c.width, textAlign: c.align }}
                    className={canSort ? 'dt-sortable' : ''} onClick={() => canSort && toggleSort(c.key)}>
                    <span className="dt-th">{c.header}
                      {active && (sortDir === 'asc' ? <ChevronUp size={13} /> : <ChevronDown size={13} />)}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 ? (
              <tr><td className="dt-state" colSpan={columns.length}>Загрузка…</td></tr>
            ) : visible.length === 0 ? (
              <tr><td className="dt-state" colSpan={columns.length}>{emptyText}</td></tr>
            ) : visible.map(row => (
              <tr key={rowKey(row)} className={onRowClick ? 'dt-clickable' : ''}
                onClick={onRowClick ? () => onRowClick(row) : undefined}>
                {columns.map(c => (
                  <td key={c.key} style={{ textAlign: c.align }}>
                    {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '—')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageSize > 0 && sorted.length > pageSize && (
        <div className="dt-pager">
          <button className="dt-pg-btn" disabled={safePage === 0} onClick={() => setPage(p => p - 1)}>← Назад</button>
          <span className="dt-pg-info">{safePage + 1} / {pageCount}</span>
          <button className="dt-pg-btn" disabled={safePage >= pageCount - 1} onClick={() => setPage(p => p + 1)}>Вперёд →</button>
        </div>
      )}
    </div>
  );
}

export default DataTable;
