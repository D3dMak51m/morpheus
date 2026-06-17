import { useState, useEffect, useCallback } from 'react';
import { Box, Group, Title, Text, Badge, Button, SegmentedControl, Anchor } from '@mantine/core';
import { ListChecks, RefreshCw, Check, X, Ban } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';

interface Decision {
  id: number; agent_id: string | null; mission_id: number | null; channel_ref: string | null;
  post_url: string | null; kind: string; detail: string | null; verdict: boolean | null; created_at: string | null;
}
interface Props { token: string; }

export default function DecisionsScreen({ token }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [rows, setRows] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState('');

  const fetchRows = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '300' });
      if (kind) params.set('kind', kind);
      const r = await fetch(`/api/v1/decisions?${params}`, { headers });
      if (r.ok) setRows((await r.json()).decisions || []);
    } finally { setLoading(false); }
  }, [kind, token]);
  useEffect(() => { fetchRows(); }, [fetchRows]);

  const verdict = (d: Decision) => {
    if (d.kind === 'skip') return <Badge color="yellow" variant="light" leftSection={<Ban size={11} />}>пропуск</Badge>;
    if (d.verdict === true) return <Badge color="teal" variant="light" leftSection={<Check size={11} />}>по теме</Badge>;
    if (d.verdict === false) return <Badge color="gray" variant="light" leftSection={<X size={11} />}>не по теме</Badge>;
    return <Badge variant="light">—</Badge>;
  };

  const columns: Col<Decision>[] = [
    { key: 'created_at', header: 'Время', minWidth: 150, sortValue: d => d.created_at || '',
      render: d => <Text size="xs" c="dimmed">{d.created_at ? new Date(d.created_at).toLocaleString('ru-RU') : '—'}</Text> },
    { key: 'agent_id', header: 'Агент', minWidth: 180, render: d => <Text size="sm" ff="monospace">{d.agent_id || '—'}</Text> },
    { key: 'channel_ref', header: 'Канал', minWidth: 160, render: d => <Text size="sm" c="blue">{d.channel_ref || '—'}</Text> },
    { key: 'verdict', header: 'Вердикт', minWidth: 130, sortValue: d => d.kind === 'skip' ? 2 : d.verdict ? 1 : 0, render: verdict },
    { key: 'detail', header: 'Что распознал / причина', minWidth: 420, sortable: false,
      render: d => <Text size="sm" lineClamp={2} maw={520}>{d.detail || '—'}</Text> },
    { key: 'post', header: '', minWidth: 70, sortable: false,
      render: d => d.post_url ? <Anchor size="xs" href={d.post_url} target="_blank">пост ↗</Anchor> : null },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><ListChecks size={22} style={{ verticalAlign: -4 }} /> Решения</Title>
          <Text size="sm" c="dimmed">Почему рой отреагировал или нет: что бот распознал, вердикт релевантности и причина пропуска.</Text>
        </div>
        <Button variant="default" leftSection={<RefreshCw size={15} />} onClick={fetchRows}>Обновить</Button>
      </Group>
      <DataView
        columns={columns} rows={rows} rowKey={d => d.id} loading={loading}
        searchText={d => `${d.agent_id || ''} ${d.channel_ref || ''} ${d.detail || ''}`}
        searchPlaceholder="🔍 Поиск по каналу, агенту, тексту…"
        emptyText="Решений пока нет."
        toolbar={<SegmentedControl size="xs" value={kind} onChange={setKind}
          data={[{ label: 'все', value: '' }, { label: 'релевантность', value: 'relevance' }, { label: 'пропуски', value: 'skip' }]} />}
      />
    </Box>
  );
}
