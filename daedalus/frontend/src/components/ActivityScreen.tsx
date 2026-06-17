import { useState, useEffect, useCallback } from 'react';
import { Box, Group, Title, Text, Badge, Anchor, SegmentedControl } from '@mantine/core';
import { Activity } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';

interface Log {
  id: number; agent_id: string; platform: string; action_type: string;
  target_url: string; text_content: string | null; status: string; created_at: string;
}
interface Props { token: string; }

const STATUS_COLOR: Record<string, string> = { SUCCESS: 'teal', FAILED: 'red', PENDING: 'yellow' };
const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads'];

export default function ActivityScreen({ token }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [logs, setLogs] = useState<Log[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState('');

  const fetchLogs = useCallback(async () => {
    try {
      const p = new URLSearchParams({ limit: '100' });
      if (platform) p.set('platform', platform);
      const r = await fetch(`/api/v1/analytics/stream?${p}`, { headers });
      if (r.ok) { const d = await r.json(); setLogs(d.logs || []); setTotal(d.total || 0); }
    } finally { setLoading(false); }
  }, [platform, token]);
  useEffect(() => { fetchLogs(); const iv = setInterval(fetchLogs, 5000); return () => clearInterval(iv); }, [fetchLogs]);

  const columns: Col<Log>[] = [
    { key: 'status', header: 'Статус', minWidth: 110, sortValue: l => l.status,
      render: l => <Badge color={STATUS_COLOR[l.status] || 'gray'} variant="light">{l.status}</Badge> },
    { key: 'created_at', header: 'Время', minWidth: 150, sortValue: l => l.created_at,
      render: l => <Text size="xs" c="dimmed">{new Date(l.created_at).toLocaleString('ru-RU')}</Text> },
    { key: 'agent_id', header: 'Агент', minWidth: 180, render: l => <Text size="sm" ff="monospace">{l.agent_id}</Text> },
    { key: 'platform', header: 'Платформа', minWidth: 110, render: l => <Badge variant="outline">{l.platform}</Badge> },
    { key: 'action_type', header: 'Действие', minWidth: 120, sortValue: l => l.action_type, render: l => l.action_type },
    { key: 'text_content', header: 'Текст', minWidth: 360, sortable: false,
      render: l => l.text_content ? <Text size="sm" lineClamp={2} maw={460}>«{l.text_content}»</Text> : <Text c="dimmed" size="sm">—</Text> },
    { key: 'target_url', header: '', minWidth: 70, sortable: false,
      render: l => l.target_url ? <Anchor size="xs" href={l.target_url} target="_blank">↗</Anchor> : null },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Activity size={22} style={{ verticalAlign: -4 }} /> Журнал активности</Title>
          <Text size="sm" c="dimmed">Логи действий всех воркеров роя в реальном времени. (всего записей: {total})</Text>
        </div>
      </Group>
      <DataView
        columns={columns} rows={logs} rowKey={l => l.id} loading={loading}
        searchText={l => `${l.agent_id} ${l.platform} ${l.action_type} ${l.text_content || ''}`}
        searchPlaceholder="🔍 Поиск по агенту, действию, тексту…"
        emptyText="Записей нет."
        toolbar={<SegmentedControl size="xs" value={platform} onChange={setPlatform}
          data={[{ label: 'все', value: '' }, ...PLATFORMS.map(p => ({ label: p, value: p }))]} />}
      />
    </Box>
  );
}
