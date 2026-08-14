import { useState, useEffect } from 'react';
import {
  Box, Group, Stack, Title, Text, Grid, Paper, ScrollArea, Textarea, Button, Table, TextInput,
  NavLink, Badge, Alert, Loader, Center, Code,
} from '@mantine/core';
import { Database, Play } from 'lucide-react';

interface DatabaseExplorerProps { token: string; }
interface TableData { table: string; columns: string[]; rows: Record<string, any>[]; total_count: number; }

const DatabaseExplorer = ({ token }: DatabaseExplorerProps) => {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState('');
  const [tableData, setTableData] = useState<TableData | null>(null);
  const [limit] = useState(100);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sqlQuery, setSqlQuery] = useState('');
  const [sqlResult, setSqlResult] = useState<any>(null);
  const [sqlError, setSqlError] = useState('');
  const [editingCell, setEditingCell] = useState<{ rowIndex: number; col: string } | null>(null);
  const [editValue, setEditValue] = useState('');

  useEffect(() => { (async () => {
    try {
      const r = await fetch('/api/v1/db/tables', { headers });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setTables(d.tables || []);
      if (d.tables?.length) setSelectedTable(d.tables[0]);
    } catch (e: any) { setError(e.message || 'Не удалось загрузить таблицы'); }
  })(); }, []);

  useEffect(() => {
    if (!selectedTable) return;
    (async () => {
      setLoading(true); setError('');
      try {
        const r = await fetch(`/api/v1/db/tables/${selectedTable}?limit=${limit}&offset=${offset}`, { headers });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setTableData(await r.json());
      } catch (e: any) { setError(e.message || 'Не удалось загрузить данные'); setTableData(null); }
      finally { setLoading(false); }
    })();
  }, [selectedTable, limit, offset]);

  const saveCell = async (rowIndex: number, col: string) => {
    if (!tableData) return;
    const row = tableData.rows[rowIndex];
    const pkCol = tableData.columns[0];
    try {
      const r = await fetch('/api/v1/db/cell', { method: 'PUT', headers, body: JSON.stringify({ table: selectedTable, primary_key_column: pkCol, primary_key_value: row[pkCol], column: col, new_value: editValue }) });
      if (!r.ok) { const e = await r.json().catch(() => null); throw new Error(e?.detail || `HTTP ${r.status}`); }
      const rows = [...tableData.rows]; rows[rowIndex] = { ...rows[rowIndex], [col]: editValue };
      setTableData({ ...tableData, rows }); setEditingCell(null);
    } catch (e: any) { setError(e.message || 'Не удалось обновить ячейку'); }
  };

  const executeSql = async () => {
    setSqlError(''); setSqlResult(null);
    if (!sqlQuery.trim()) return;
    try {
      const r = await fetch('/api/v1/db/query', { method: 'POST', headers, body: JSON.stringify({ sql: sqlQuery }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      setSqlResult(d);
    } catch (e: any) { setSqlError(e.message || 'Запрос не выполнен'); }
  };

  return (
    <Box p="lg">
      <Group mb="md"><Title order={2}><Database size={22} style={{ verticalAlign: -4 }} /> База данных</Title></Group>
      <Text size="sm" c="dimmed" mb="md">Прямой доступ к таблицам PostgreSQL и выполнение SQL.</Text>
      {error && <Alert color="red" variant="light" mb="md">{error}</Alert>}

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Paper withBorder radius="md" p="xs">
            <Text size="xs" tt="uppercase" c="dimmed" fw={600} mb="xs" px="xs">Таблицы</Text>
            <ScrollArea.Autosize mah="70vh">
              {tables.map(t => (
                <NavLink key={t} label={<Text size="sm" ff="monospace">{t}</Text>} active={t === selectedTable}
                  onClick={() => { setSelectedTable(t); setOffset(0); }} />
              ))}
            </ScrollArea.Autosize>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 9 }}>
          <Stack gap="md">
            <Paper withBorder radius="md" p="md">
              <Text fw={600} mb="xs">SQL-консоль (SuperAdmin)</Text>
              <Textarea value={sqlQuery} onChange={e => setSqlQuery(e.currentTarget.value)} placeholder="SELECT * FROM roles WHERE id = 1;" autosize minRows={3} styles={{ input: { fontFamily: 'monospace' } }} />
              <Button mt="sm" leftSection={<Play size={15} />} onClick={executeSql}>Выполнить запрос</Button>
              {sqlError && <Alert color="red" variant="light" mt="sm">{sqlError}</Alert>}
              {sqlResult && (
                <Box mt="md">
                  <Text size="sm" c="dimmed" mb="xs">Строк возвращено: {sqlResult.row_count}</Text>
                  {sqlResult.rows.length > 0 && (
                    <Table.ScrollContainer minWidth={sqlResult.columns.length * 140} type="native" mah={320}>
                      <Table striped withTableBorder stickyHeader>
                        <Table.Thead><Table.Tr>{sqlResult.columns.map((c: string) => <Table.Th key={c}>{c}</Table.Th>)}</Table.Tr></Table.Thead>
                        <Table.Tbody>{sqlResult.rows.map((r: any, i: number) => <Table.Tr key={i}>{sqlResult.columns.map((c: string) => <Table.Td key={c}><Text size="xs" style={{ whiteSpace: 'nowrap' }}>{String(r[c])}</Text></Table.Td>)}</Table.Tr>)}</Table.Tbody>
                      </Table>
                    </Table.ScrollContainer>
                  )}
                </Box>
              )}
            </Paper>

            <Paper withBorder radius="md" p="md">
              <Group justify="space-between" mb="sm">
                <Group gap="xs"><Text fw={600} ff="monospace">{selectedTable}</Text><Badge variant="light">{tableData?.total_count || 0} строк</Badge></Group>
                <Group gap="xs">
                  <Button size="xs" variant="default" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>← Назад</Button>
                  <Text size="xs" c="dimmed">offset: {offset}</Text>
                  <Button size="xs" variant="default" disabled={!tableData || offset + limit >= tableData.total_count} onClick={() => setOffset(offset + limit)}>Вперёд →</Button>
                </Group>
              </Group>
              {loading ? <Center py="xl"><Loader /></Center> : tableData?.rows.length === 0 ? <Text c="dimmed" ta="center" py="lg">Таблица пуста.</Text> : (
                <Table.ScrollContainer minWidth={(tableData?.columns.length || 1) * 160} type="native">
                  <Table striped withTableBorder stickyHeader highlightOnHover>
                    <Table.Thead><Table.Tr>{tableData?.columns.map(c => <Table.Th key={c}>{c}</Table.Th>)}</Table.Tr></Table.Thead>
                    <Table.Tbody>{tableData?.rows.map((row, ri) => (
                      <Table.Tr key={ri}>{tableData.columns.map(col => {
                        const isEditing = editingCell?.rowIndex === ri && editingCell?.col === col;
                        const val = row[col]; const isPk = col === tableData.columns[0];
                        return (
                          <Table.Td key={col} onDoubleClick={() => { if (!isPk) { setEditingCell({ rowIndex: ri, col }); setEditValue(val === null ? '' : String(val)); } }} style={{ whiteSpace: 'nowrap', maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {isEditing ? (
                              <TextInput autoFocus size="xs" value={editValue} onChange={e => setEditValue(e.currentTarget.value)} onBlur={() => saveCell(ri, col)}
                                onKeyDown={e => { if (e.key === 'Enter') saveCell(ri, col); if (e.key === 'Escape') setEditingCell(null); }} />
                            ) : val === null ? <Text span size="xs" c="dimmed" fs="italic">NULL</Text> : <Text span size="xs">{String(val)}</Text>}
                          </Table.Td>
                        );
                      })}</Table.Tr>
                    ))}</Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              )}
              <Text size="xs" c="dimmed" mt="xs">Двойной клик по ячейке — редактирование (нужно право db:edit). Первичные ключи нельзя менять inline. <Code>Enter</Code> — сохранить, <Code>Esc</Code> — отмена.</Text>
            </Paper>
          </Stack>
        </Grid.Col>
      </Grid>
    </Box>
  );
};

export default DatabaseExplorer;
