import { useState, useEffect, useCallback } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, Switch, Select, TextInput, Chip,
} from '@mantine/core';
import { Map, Plus, RefreshCw, RotateCw, Trash2 } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';

interface LandscapeTarget {
  id: number; platform: string; type: string; target_identifier: string;
  is_active: boolean; associated_tags: string[] | null; default_layers: string[];
}
interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; }

const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads', 'facebook', 'web', 'rss'];
const TYPES = ['channel', 'feed', 'url'];
const LAYERS = ['global', 'regional', 'state', 'city', 'personal'];
const LAYER_COLOR: Record<string, string> = { global: 'blue', regional: 'violet', state: 'pink', city: 'orange', personal: 'teal' };

export default function LandscapeScreen({ token, selectedId, onOpen, onBack }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [targets, setTargets] = useState<LandscapeTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchTargets = useCallback(async () => {
    setLoading(true);
    try { const r = await fetch('/api/v1/landscape/', { headers }); if (r.ok) { setTargets(await r.json()); setError(''); } }
    catch (e: any) { setError(e.message || 'Не удалось загрузить источники'); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchTargets(); }, [fetchTargets]);

  const toggleActive = async (t: LandscapeTarget) => {
    setTargets(prev => prev.map(x => x.id === t.id ? { ...x, is_active: !x.is_active } : x));
    await fetch(`/api/v1/landscape/${t.id}`, { method: 'PUT', headers, body: JSON.stringify({ is_active: !t.is_active }) });
  };
  const forceSync = async () => { await fetch('/api/v1/huginn/force-sync', { method: 'POST', headers }); alert('Сигнал синхронизации отправлен в HUGINN.'); };

  if (selectedId === 'new') {
    return <SourceForm token={token} target={null} onBack={onBack} onSaved={() => { fetchTargets(); onBack(); }} />;
  }
  if (selectedId) {
    const t = targets.find(x => String(x.id) === selectedId);
    if (!t) return <DetailPage onBack={onBack} title="Загрузка…"><Text c="dimmed">Источник загружается…</Text></DetailPage>;
    return <SourceForm token={token} target={t} onBack={onBack} onSaved={() => { fetchTargets(); onBack(); }} onDeleted={() => { fetchTargets(); onBack(); }} />;
  }

  const columns: Col<LandscapeTarget>[] = [
    { key: 'platform', header: 'Платформа', minWidth: 110, render: t => <Badge variant="outline">{t.platform}</Badge> },
    { key: 'type', header: 'Тип', minWidth: 90, sortValue: t => t.type || 'channel', render: t => t.type || 'channel' },
    { key: 'target_identifier', header: 'Источник', minWidth: 280, sortValue: t => t.target_identifier,
      render: t => <Text ff="monospace" size="sm">{t.target_identifier}</Text> },
    { key: 'default_layers', header: 'Слои', minWidth: 180, sortable: false,
      render: t => <Group gap={4}>{(t.default_layers || ['global']).map(l => <Badge key={l} size="sm" color={LAYER_COLOR[l] || 'gray'} variant="light">{l}</Badge>)}</Group> },
    { key: 'tags', header: 'Теги', minWidth: 160, sortable: false,
      render: t => <Group gap={4}>{(t.associated_tags || []).map(tag => <Badge key={tag} size="sm" variant="dot">{tag}</Badge>)}</Group> },
    { key: 'is_active', header: 'Статус', minWidth: 90, sortValue: t => t.is_active ? 1 : 0,
      render: t => <Box onClick={e => e.stopPropagation()}><Switch checked={t.is_active} onChange={() => toggleActive(t)} size="sm" /></Box> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Map size={22} style={{ verticalAlign: -4 }} /> Ландшафт скрапинга</Title>
          <Text size="sm" c="dimmed">Источники сбора новостей в базу знаний — нажмите на строку, чтобы открыть и отредактировать.</Text>
        </div>
        <Group gap="xs">
          <Button variant="default" leftSection={<RotateCw size={15} />} onClick={forceSync}>Синхр. HUGINN</Button>
          <Button variant="default" leftSection={<RefreshCw size={15} />} onClick={fetchTargets}>Обновить</Button>
          <Button leftSection={<Plus size={16} />} onClick={() => onOpen('new')}>Добавить источник</Button>
        </Group>
      </Group>
      {error && <Text c="red" mb="sm">{error}</Text>}
      <DataView
        columns={columns} rows={targets} rowKey={t => t.id} loading={loading}
        searchText={t => `${t.platform} ${t.type || ''} ${t.target_identifier} ${(t.associated_tags || []).join(' ')}`}
        searchPlaceholder="🔍 Поиск по источнику, платформе, типу, тегу…"
        emptyText="Источников нет — добавьте первый."
        onRowClick={t => onOpen(String(t.id))}
      />
    </Box>
  );
}

function SourceForm({ token, target, onBack, onSaved, onDeleted }: {
  token: string; target: LandscapeTarget | null; onBack: () => void; onSaved: () => void; onDeleted?: () => void;
}) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const isNew = !target;
  const [platform, setPlatform] = useState(target?.platform || PLATFORMS[0]);
  const [type, setType] = useState(target?.type || TYPES[0]);
  const [layers, setLayers] = useState<string[]>(target?.default_layers?.length ? target.default_layers : ['global']);
  const [identifier, setIdentifier] = useState(target?.target_identifier || '');
  const [tags, setTags] = useState((target?.associated_tags || []).join(', '));
  const [active, setActive] = useState(target?.is_active ?? true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const toggleLayer = (l: string) => setLayers(prev => prev.includes(l) ? prev.filter(x => x !== l) : [...prev, l]);

  const save = async () => {
    if (layers.length === 0) { setError('Выберите хотя бы один слой.'); return; }
    setSaving(true); setError('');
    const tagList = tags.split(',').map(s => s.trim()).filter(Boolean);
    const body = { platform, type, default_layers: layers, target_identifier: identifier, associated_tags: tagList.length ? tagList : null, is_active: active };
    try {
      const r = await fetch(isNew ? '/api/v1/landscape/' : `/api/v1/landscape/${target!.id}`, { method: isNew ? 'POST' : 'PUT', headers, body: JSON.stringify(body) });
      if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `HTTP ${r.status}`); }
      onSaved();
    } catch (e: any) { setError(e.message || 'Не удалось сохранить'); }
    finally { setSaving(false); }
  };
  const remove = async () => {
    if (!target || !confirm('Удалить этот источник?')) return;
    const r = await fetch(`/api/v1/landscape/${target.id}`, { method: 'DELETE', headers });
    if (r.ok) onDeleted?.();
  };

  return (
    <DetailPage
      onBack={onBack}
      title={isNew ? 'Новый источник' : 'Источник'}
      subtitle={!isNew ? <Text span ff="monospace" size="sm" c="dimmed">{target!.target_identifier}</Text> : undefined}
      footer={<>{error && <Text c="red" size="sm" mr="auto">{error}</Text>}
        {!isNew && <Button variant="subtle" color="red" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить</Button>}
        <Button variant="default" onClick={onBack}>Отмена</Button>
        <Button loading={saving} disabled={!identifier.trim()} onClick={save}>{isNew ? 'Создать' : 'Сохранить'}</Button></>}
    >
      <Stack gap="md" maw={620}>
        <Group grow>
          <Select label="Платформа" data={PLATFORMS} value={platform} onChange={v => v && setPlatform(v)} />
          <Select label="Тип" data={TYPES} value={type} onChange={v => v && setType(v)} />
        </Group>
        <Box>
          <Text size="sm" mb={6}>Слои по умолчанию <Text span c="red">*</Text></Text>
          <Group gap="xs">{LAYERS.map(l => <Chip key={l} checked={layers.includes(l)} onChange={() => toggleLayer(l)} color={LAYER_COLOR[l]}>{l}</Chip>)}</Group>
          <Text size="xs" c="dimmed" mt={4}>Факты из этого источника получают эти слои; LLM-классификатор может добавить ещё.</Text>
        </Box>
        <TextInput label="Идентификатор источника" required value={identifier} onChange={e => setIdentifier(e.currentTarget.value)} placeholder="напр. @username, channel_id или url" />
        <TextInput label="Теги (через запятую)" value={tags} onChange={e => setTags(e.currentTarget.value)} placeholder="крипто, политика, технологии" />
        <Switch label="Активен" checked={active} onChange={e => setActive(e.currentTarget.checked)} />
      </Stack>
    </DetailPage>
  );
}
