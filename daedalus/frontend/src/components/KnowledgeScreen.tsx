import { useState, useEffect, useCallback } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, Textarea, TextInput, Chip, Paper, SimpleGrid, Anchor,
} from '@mantine/core';
import { Brain, RefreshCw, Plus, Trash2 } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';

interface Fact {
  id: number; content: string; source_url: string | null; landscape_layers: string[];
  categories: string[]; tags: string[]; sources: string[] | null; source_count: number;
  timestamp: number; created_at: string; updated_at: string;
}
interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; }

const LAYERS = ['global', 'regional', 'state', 'city', 'personal'];
const LAYER_COLOR: Record<string, string> = { global: 'blue', regional: 'violet', state: 'pink', city: 'orange', personal: 'teal' };

function sourceLabel(url: string | null): string {
  if (!url) return '—';
  if (url.startsWith('manual://')) return 'ручной ввод';
  const tg = url.match(/t\.me\/(?:c\/)?@?([A-Za-z0-9_]+)/);
  if (tg) return '@' + tg[1];
  if (url.startsWith('@')) return url;
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url.slice(0, 32); }
}

export default function KnowledgeScreen({ token, selectedId, onOpen, onBack }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [facts, setFacts] = useState<Fact[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetchFacts = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/v1/knowledge/facts?limit=500', { headers });
      if (r.ok) { const d = await r.json(); setFacts(d.facts || []); setTotal(d.total ?? 0); }
      const sr = await fetch('/api/v1/knowledge/stats', { headers });
      if (sr.ok) setStats((await sr.json()).by_layer || {});
    } finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchFacts(); }, [fetchFacts]);

  const remove = async (id: number) => {
    const r = await fetch(`/api/v1/knowledge/facts/${id}`, { method: 'DELETE', headers });
    if (r.ok) { setFacts(prev => prev.filter(f => f.id !== id)); }
  };

  if (selectedId === 'new') return <InjectForm token={token} onBack={onBack} onSaved={() => { fetchFacts(); onBack(); }} />;
  if (selectedId) {
    const f = facts.find(x => String(x.id) === selectedId);
    if (!f) return <DetailPage onBack={onBack} title="Загрузка…"><Text c="dimmed">Факт загружается…</Text></DetailPage>;
    return <FactDetail fact={f} onBack={onBack} onDelete={async () => { await remove(f.id); onBack(); }} />;
  }

  const columns: Col<Fact>[] = [
    { key: 'id', header: 'ID', minWidth: 60, sortValue: f => f.id, render: f => <Text size="xs" c="dimmed">{f.id}</Text> },
    { key: 'layers', header: 'Слои', minWidth: 150, sortable: false,
      render: f => <Group gap={4}>{(f.landscape_layers || []).map(l => <Badge key={l} size="sm" color={LAYER_COLOR[l] || 'gray'} variant="light">{l}</Badge>)}</Group> },
    { key: 'content', header: 'Факт', minWidth: 420, sortable: false, render: f => <Text size="sm" lineClamp={2} maw={520}>{f.content}</Text> },
    { key: 'tags', header: 'Категории / теги', minWidth: 200, sortable: false,
      render: f => <Group gap={4}>{(f.categories || []).map(c => <Badge key={c} size="sm" variant="outline">{c}</Badge>)}{(f.tags || []).map(t => <Badge key={t} size="sm" variant="dot">#{t}</Badge>)}</Group> },
    { key: 'source', header: 'Источник', minWidth: 140, sortValue: f => sourceLabel(f.source_url),
      render: f => <Group gap={4}><Text size="sm">{sourceLabel(f.source_url)}</Text>{f.source_count > 1 && <Badge size="xs" variant="light">×{f.source_count}</Badge>}</Group> },
    { key: 'updated_at', header: 'Обновлён', minWidth: 150, sortValue: f => f.updated_at,
      render: f => <Text size="xs" c="dimmed">{new Date(f.updated_at).toLocaleString('ru-RU')}</Text> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="sm">
        <div>
          <Title order={2}><Brain size={22} style={{ verticalAlign: -4 }} /> Знания роя</Title>
          <Text size="sm" c="dimmed">Дедуплицированные факты (RAG), которыми пользуется ORPHEUS при ответах. Нажмите на строку, чтобы открыть факт.</Text>
        </div>
        <Group gap="xs">
          <Button variant="default" leftSection={<RefreshCw size={15} />} onClick={fetchFacts}>Обновить</Button>
          <Button leftSection={<Plus size={16} />} onClick={() => onOpen('new')}>Добавить факт</Button>
        </Group>
      </Group>
      <SimpleGrid cols={{ base: 3, sm: 6 }} mb="md">
        <Paper withBorder p="xs" radius="md" ta="center"><Text size="xs" c="dimmed">всего</Text><Text fw={700}>{total}</Text></Paper>
        {LAYERS.map(l => <Paper key={l} withBorder p="xs" radius="md" ta="center" style={{ borderTop: `2px solid var(--mantine-color-${LAYER_COLOR[l]}-6)` }}><Text size="xs" c="dimmed">{l}</Text><Text fw={700}>{stats[l] ?? 0}</Text></Paper>)}
      </SimpleGrid>
      <DataView
        columns={columns} rows={facts} rowKey={f => f.id} loading={loading}
        searchText={f => `${f.content} ${(f.categories || []).join(' ')} ${(f.tags || []).join(' ')} ${sourceLabel(f.source_url)}`}
        searchPlaceholder="🔍 Поиск по тексту факта, категории, тегу, источнику…"
        emptyText="Фактов нет."
        onRowClick={f => onOpen(String(f.id))}
      />
    </Box>
  );
}

function FactDetail({ fact, onBack, onDelete }: { fact: Fact; onBack: () => void; onDelete: () => void }) {
  return (
    <DetailPage onBack={onBack} title={`Факт #${fact.id}`}
      subtitle={<Text span size="sm" c="dimmed">источник: {sourceLabel(fact.source_url)}{fact.source_count > 1 ? ` · ×${fact.source_count}` : ''}</Text>}
      footer={<><Button variant="default" onClick={onBack}>Назад</Button><Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={onDelete}>Удалить факт</Button></>}>
      <Stack gap="md" maw={820}>
        <Paper withBorder p="md" radius="md"><Text>{fact.content}</Text></Paper>
        <Group gap="xs">{(fact.landscape_layers || []).map(l => <Badge key={l} color={LAYER_COLOR[l] || 'gray'} variant="light">{l}</Badge>)}</Group>
        <Group gap="xs">{(fact.categories || []).map(c => <Badge key={c} variant="outline">{c}</Badge>)}{(fact.tags || []).map(t => <Badge key={t} variant="dot">#{t}</Badge>)}</Group>
        {fact.source_url && <Text size="sm">Источник: <Anchor href={fact.source_url} target="_blank">{fact.source_url}</Anchor></Text>}
        {fact.sources && fact.sources.length > 1 && <Paper withBorder p="sm" radius="md"><Text size="xs" c="dimmed" mb={4}>Кластер источников ({fact.sources.length})</Text>{fact.sources.map((s, i) => <Text key={i} size="xs" c="dimmed">{s}</Text>)}</Paper>}
        <Text size="xs" c="dimmed">создан {new Date(fact.created_at).toLocaleString('ru-RU')} · обновлён {new Date(fact.updated_at).toLocaleString('ru-RU')}</Text>
      </Stack>
    </DetailPage>
  );
}

function InjectForm({ token, onBack, onSaved }: { token: string; onBack: () => void; onSaved: () => void }) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [content, setContent] = useState('');
  const [layers, setLayers] = useState<string[]>(['global']);
  const [source, setSource] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const save = async () => {
    if (!content.trim()) { setError('Введите текст факта.'); return; }
    if (layers.length === 0) { setError('Выберите хотя бы один слой.'); return; }
    setSaving(true); setError('');
    try {
      const r = await fetch('/api/v1/knowledge/facts/inject', { method: 'POST', headers, body: JSON.stringify({ content, layers, source_url: source.trim() || 'manual://operator-injection' }) });
      if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `HTTP ${r.status}`); }
      onSaved();
    } catch (e: any) { setError(e.message || 'Не удалось добавить факт'); }
    finally { setSaving(false); }
  };

  return (
    <DetailPage onBack={onBack} title="Добавить факт в память"
      footer={<>{error && <Text c="red" size="sm" mr="auto">{error}</Text>}<Button variant="default" onClick={onBack}>Отмена</Button><Button loading={saving} onClick={save}>Добавить в память</Button></>}>
      <Stack gap="md" maw={720}>
        <Text size="sm" c="dimmed">Daedalus авто-классифицирует (категории/теги), строит эмбеддинг (nomic-embed-text) и кластеризует (близость &gt; 0.85 — слияние с существующим фактом).</Text>
        <Textarea label="Текст факта" autosize minRows={5} required value={content} onChange={e => setContent(e.currentTarget.value)} placeholder="напр. Ташкентское метро продлило Юнусабадскую линию 2026-06-01." />
        <Box>
          <Text size="sm" mb={6}>Слои <Text span c="red">*</Text></Text>
          <Group gap="xs">{LAYERS.map(l => <Chip key={l} checked={layers.includes(l)} onChange={() => setLayers(s => s.includes(l) ? s.filter(x => x !== l) : [...s, l])} color={LAYER_COLOR[l]}>{l}</Chip>)}</Group>
        </Box>
        <TextInput label="Источник (необязательно)" value={source} onChange={e => setSource(e.currentTarget.value)} placeholder="https://… или пусто" />
      </Stack>
    </DetailPage>
  );
}
