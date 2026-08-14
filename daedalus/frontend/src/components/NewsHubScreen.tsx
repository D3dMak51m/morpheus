import { useState, useEffect, useCallback } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, Select, Textarea, Chip, Paper, SegmentedControl,
} from '@mantine/core';
import { Radio, RefreshCw, Play, Pause, Check, X } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';

interface CapturedEvent {
  id: number; event_id: string; source_platform: string; source_target: string; post_id: string;
  text_content: string | null; media_type: string | null; media_path: string | null;
  layers: Record<string, unknown>; timestamp: number; status: string;
}
interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; }

const STATUS_LABEL: Record<string, string> = { pending: 'в очереди', approved: 'одобрено', rejected: 'отклонено', Processed: 'обработано' };
const STATUS_COLOR: Record<string, string> = { pending: 'yellow', approved: 'teal', rejected: 'gray', Processed: 'blue' };
const LAYER_KEYS = ['global', 'region', 'state', 'city', 'personal'];

const activeLayers = (layers: Record<string, unknown>): string[] => {
  if (!layers) return [];
  const out: string[] = [];
  for (const [k, v] of Object.entries(layers)) {
    if (k === 'personal_tags' && Array.isArray(v)) out.push(...(v as string[]));
    else if (v === true) out.push(k);
  }
  return out;
};

export default function NewsHubScreen({ token, selectedId, onOpen, onBack }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [events, setEvents] = useState<CapturedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(true);
  const [statusFilter, setStatusFilter] = useState('');

  const fetchEvents = useCallback(async () => {
    try { const r = await fetch('/api/v1/huginn/captured-events?limit=200', { headers }); if (r.ok) setEvents((await r.json()).events || []); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchEvents(); let iv: any; if (live) iv = setInterval(fetchEvents, 5000); return () => clearInterval(iv); }, [fetchEvents, live]);

  const updateStatus = async (id: number, status: string) => {
    const r = await fetch(`/api/v1/huginn/captured-events/${id}`, { method: 'PUT', headers, body: JSON.stringify({ status }) });
    if (r.ok) setEvents(prev => prev.map(e => e.id === id ? { ...e, status } : e));
  };

  if (selectedId) {
    const ev = events.find(e => String(e.id) === selectedId);
    if (!ev) return <DetailPage onBack={onBack} title="Загрузка…"><Text c="dimmed">Событие загружается…</Text></DetailPage>;
    return <EventDetail key={ev.id} token={token} event={ev} onBack={onBack} onSaved={() => fetchEvents()} />;
  }

  const shown = statusFilter ? events.filter(e => e.status === statusFilter) : events;
  const columns: Col<CapturedEvent>[] = [
    { key: 'source_platform', header: 'Платформа', minWidth: 110, render: e => <Badge variant="outline">{e.source_platform}</Badge> },
    { key: 'source_target', header: 'Источник', minWidth: 180, sortValue: e => e.source_target,
      render: e => <Text size="sm" lineClamp={1} maw={180} title={e.source_target}>{e.source_target}</Text> },
    { key: 'text_content', header: 'Текст', minWidth: 360, sortable: false,
      render: e => e.text_content ? <Text size="sm" lineClamp={2} maw={460}>{e.text_content}</Text> : <Text c="dimmed" size="sm">[медиа: {e.media_type || '—'}]</Text> },
    { key: 'layers', header: 'Слои', minWidth: 140, sortable: false,
      render: e => <Group gap={4}>{activeLayers(e.layers).map(l => <Badge key={l} size="sm" variant="light">{l}</Badge>)}</Group> },
    { key: 'timestamp', header: 'Время', minWidth: 150, sortValue: e => e.timestamp,
      render: e => <Text size="xs" c="dimmed">{new Date(e.timestamp * 1000).toLocaleString('ru-RU')}</Text> },
    { key: 'status', header: 'Статус', minWidth: 110, sortValue: e => e.status,
      render: e => <Badge color={STATUS_COLOR[e.status] || 'gray'} variant="light">{STATUS_LABEL[e.status] || e.status}</Badge> },
    { key: 'actions', header: '', minWidth: 90, sortable: false, align: 'right',
      render: e => <Group gap={4} justify="flex-end" onClick={ev => ev.stopPropagation()}>
        <Button size="xs" variant="subtle" color="teal" onClick={() => updateStatus(e.id, 'approved')}><Check size={14} /></Button>
        <Button size="xs" variant="subtle" color="orange" onClick={() => updateStatus(e.id, 'rejected')}><X size={14} /></Button>
      </Group> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Radio size={22} style={{ verticalAlign: -4 }} /> Центр HUGINN</Title>
          <Text size="sm" c="dimmed">Перехваченные события сбора новостей — нажмите на строку, чтобы открыть и отредактировать.</Text>
        </div>
        <Group gap="xs">
          <Button variant={live ? 'light' : 'default'} color={live ? 'teal' : undefined} leftSection={live ? <Pause size={15} /> : <Play size={15} />} onClick={() => setLive(v => !v)}>{live ? 'Пауза' : 'Live'}</Button>
          <Button variant="default" leftSection={<RefreshCw size={15} />} onClick={fetchEvents}>Обновить</Button>
        </Group>
      </Group>
      <DataView
        columns={columns} rows={shown} rowKey={e => e.id} loading={loading}
        searchText={e => `${e.source_platform} ${e.source_target} ${e.text_content || ''}`}
        searchPlaceholder="🔍 Поиск по тексту, источнику, платформе…"
        emptyText="Событий пока нет."
        onRowClick={e => onOpen(String(e.id))}
        toolbar={<SegmentedControl size="xs" value={statusFilter} onChange={setStatusFilter}
          data={[{ label: 'все', value: '' }, { label: 'в очереди', value: 'pending' }, { label: 'обработано', value: 'Processed' }]} />}
      />
    </Box>
  );
}

function EventDetail({ token, event, onBack, onSaved }: { token: string; event: CapturedEvent; onBack: () => void; onSaved: () => void; }) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [text, setText] = useState(event.text_content || '');
  const [status, setStatus] = useState(event.status);
  const [layers, setLayers] = useState<Record<string, boolean>>(() => {
    const o: Record<string, boolean> = {}; for (const k of LAYER_KEYS) o[k] = event.layers?.[k] === true; return o;
  });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    const r = await fetch(`/api/v1/huginn/captured-events/${event.id}`, { method: 'PUT', headers, body: JSON.stringify({ text_content: text, status, layers }) });
    setSaving(false);
    if (r.ok) { onSaved(); onBack(); }
  };

  return (
    <DetailPage
      onBack={onBack}
      title="Событие HUGINN"
      subtitle={<Text span ff="monospace" size="sm" c="dimmed">{event.source_platform} · {event.source_target} · {event.event_id}</Text>}
      headerRight={<Badge size="lg" color={STATUS_COLOR[event.status] || 'gray'} variant="light">{STATUS_LABEL[event.status] || event.status}</Badge>}
      footer={<><Button variant="default" onClick={onBack}>Отмена</Button><Button loading={saving} onClick={save}>Сохранить</Button></>}
    >
      <Stack gap="md" maw={760}>
        <Textarea label="Текст (переопределить)" autosize minRows={5} value={text} onChange={e => setText(e.currentTarget.value)} />
        <Select label="Статус маршрутизации" w={360} value={status} onChange={v => v && setStatus(v)}
          data={[{ value: 'pending', label: 'В очереди (передать ORPHEUS)' }, { value: 'Processed', label: 'Обработано' }, { value: 'approved', label: 'Одобрено (приоритет)' }, { value: 'rejected', label: 'Отклонено (отбросить)' }]} />
        <Box>
          <Text size="sm" mb={6}>Слои маршрутизации</Text>
          <Group gap="xs">{LAYER_KEYS.map(l => <Chip key={l} checked={!!layers[l]} onChange={() => setLayers(s => ({ ...s, [l]: !s[l] }))}>{l}</Chip>)}</Group>
        </Box>
        <Paper withBorder p="md" radius="md">
          <Text size="xs" c="dimmed" tt="uppercase" fw={600} mb={4}>Метаданные</Text>
          <Text size="sm">Платформа: {event.source_platform} · Источник: {event.source_target}</Text>
          <Text size="sm">Пост: {event.post_id} · Медиа: {event.media_type || 'нет'}</Text>
          <Text size="sm" c="dimmed">{new Date(event.timestamp * 1000).toLocaleString('ru-RU')}</Text>
        </Paper>
      </Stack>
    </DetailPage>
  );
}
