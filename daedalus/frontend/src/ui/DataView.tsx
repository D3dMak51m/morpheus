import { ReactNode, useMemo, useState } from 'react';
import { Table, TextInput, Group, Text, Pagination, Center, Loader, UnstyledButton, Box } from '@mantine/core';
import { Search, ChevronUp, ChevronDown } from 'lucide-react';

export interface Col<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  sortable?: boolean;
  width?: number | string;
  align?: 'left' | 'right' | 'center';
  /** min cell width — drives the table's overall min width so wide tables h-scroll. */
  minWidth?: number;
}

interface DataViewProps<T> {
  columns: Col<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  loading?: boolean;
  searchText?: (row: T) => string;
  searchPlaceholder?: string;
  emptyText?: string;
  pageSize?: number;
  /** Extra toolbar controls (filters) shown next to the search box. */
  toolbar?: ReactNode;
  onRowClick?: (row: T) => void;
  /** Total min table width (px) for horizontal scroll; defaults to sum of column minWidths. */
  minTableWidth?: number;
}

/**
 * Mantine-based data grid: search, sortable columns, pagination, sticky header and
 * horizontal scroll (Table.ScrollContainer). The redesign's list primitive — rows are
 * clickable to open a full-screen detail page.
 */
export function DataView<T>({
  columns, rows, rowKey, loading, searchText, searchPlaceholder = 'Поиск…',
  emptyText = 'Ничего не найдено.', pageSize = 25, toolbar, onRowClick, minTableWidth,
}: DataViewProps<T>) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);

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
  const safePage = Math.min(page, pageCount);
  const visible = pageSize > 0 ? sorted.slice((safePage - 1) * pageSize, (safePage - 1) * pageSize + pageSize) : sorted;

  const toggleSort = (key: string) => {
    const col = colByKey[key];
    if (col && col.sortable === false) return;
    if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('asc'); }
  };

  const minW = minTableWidth ?? columns.reduce((s, c) => s + (c.minWidth ?? 140), 0);

  return (
    <Box>
      <Group justify="space-between" mb="sm" gap="sm" wrap="wrap">
        <Group gap="sm" wrap="wrap">
          {searchText && (
            <TextInput
              leftSection={<Search size={15} />}
              placeholder={searchPlaceholder}
              value={query}
              onChange={e => { setQuery(e.currentTarget.value); setPage(1); }}
              w={320}
            />
          )}
          {toolbar}
        </Group>
        <Text size="sm" c="dimmed">{sorted.length} запис.</Text>
      </Group>

      <Table.ScrollContainer minWidth={minW} type="native">
        <Table stickyHeader highlightOnHover={!!onRowClick} verticalSpacing="sm" striped>
          <Table.Thead>
            <Table.Tr>
              {columns.map(c => {
                const active = sortKey === c.key;
                const canSort = c.sortable !== false;
                return (
                  <Table.Th key={c.key} style={{ width: c.width, minWidth: c.minWidth, textAlign: c.align }}>
                    {canSort ? (
                      <UnstyledButton onClick={() => toggleSort(c.key)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontWeight: 600, fontSize: '0.8rem' }}>
                        {c.header}
                        {active && (sortDir === 'asc' ? <ChevronUp size={13} /> : <ChevronDown size={13} />)}
                      </UnstyledButton>
                    ) : <span style={{ fontWeight: 600, fontSize: '0.8rem' }}>{c.header}</span>}
                  </Table.Th>
                );
              })}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {loading && rows.length === 0 ? (
              <Table.Tr><Table.Td colSpan={columns.length}><Center py="lg"><Loader size="sm" /></Center></Table.Td></Table.Tr>
            ) : visible.length === 0 ? (
              <Table.Tr><Table.Td colSpan={columns.length}><Text c="dimmed" ta="center" py="lg">{emptyText}</Text></Table.Td></Table.Tr>
            ) : visible.map(row => (
              <Table.Tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={onRowClick ? { cursor: 'pointer' } : undefined}>
                {columns.map(c => (
                  <Table.Td key={c.key} style={{ textAlign: c.align }}>
                    {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '—')}
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {pageSize > 0 && sorted.length > pageSize && (
        <Group justify="center" mt="md">
          <Pagination value={safePage} onChange={setPage} total={pageCount} size="sm" />
        </Group>
      )}
    </Box>
  );
}

export default DataView;
