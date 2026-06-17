import { useState, useEffect, useCallback } from 'react';
import {
  Group, Stack, Text, Badge, Button, Tabs, TextInput, SegmentedControl, Switch, Paper, Anchor, Loader, Center,
} from '@mantine/core';
import { Search, RotateCw } from 'lucide-react';
import { DetailPage } from '../ui/DetailPage';
import { DataView, Col } from '../ui/DataView';

interface Channel {
  chat_id: string; title: string; username: string | null; type: string;
  members: number | null; role: 'target' | 'news' | 'ignored'; watching: boolean; synced_at?: string | null;
}
interface ActionLog { id: number; action_type: string; target_url: string; text_content: string | null; status: string; created_at: string; }
interface ChannelManagerProps { token: string; agentId: string; label: string; onClose: () => void; }

const ROLES: Array<'target' | 'news' | 'ignored'> = ['target', 'news', 'ignored'];
const ROLE_LABEL: Record<string, string> = { target: 'Цель', news: 'Новости', ignored: 'Игнор' };
const ROLE_COLOR: Record<string, string> = { target: 'indigo', news: 'teal', ignored: 'gray' };

const ChannelManager = ({ token, agentId, label, onClose }: ChannelManagerProps) => {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [channels, setChannels] = useState<Channel[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [actions, setActions] = useState<ActionLog[]>([]);
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [watchFilter, setWatchFilter] = useState('all');

  const apply = (data: any) => {
    setChannels(data.channels || []); setCounts(data.counts || {}); setSyncedAt(data.synced_at || null);
    setError(data.error ? `Сессия недоступна: ${data.error}` : '');
  };
  const fetchChannels = useCallback(async () => {
    setLoading(true);
    try { const r = await fetch(`/api/v1/souls/agents/${agentId}/channels`, { headers }); if (!r.ok) throw new Error(`HTTP ${r.status}`); apply(await r.json()); }
    catch (e: any) { setError(e.message || 'Ошибка загрузки'); }
    setLoading(false);
  }, [agentId, token]);
  const refresh = async () => {
    setRefreshing(true); setError('');
    try { const r = await fetch(`/api/v1/souls/agents/${agentId}/channels/sync`, { method: 'POST', headers }); if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `HTTP ${r.status}`); } apply(await r.json()); }
    catch (e: any) { setError(e.message || 'Не удалось обновить'); }
    setRefreshing(false);
  };
  const fetchActions = useCallback(async () => {
    try { const r = await fetch(`/api/v1/analytics/stream?agent_id=${encodeURIComponent(agentId)}&limit=25`, { headers }); if (r.ok) setActions((await r.json()).logs || []); } catch { /* */ }
  }, [agentId, token]);
  useEffect(() => { fetchChannels(); fetchActions(); }, [fetchChannels, fetchActions]);

  const recount = () => setChannels(prev => { setCounts(ROLES.reduce((a, r) => ({ ...a, [r]: prev.filter(c => c.role === r).length }), {})); return prev; });
  const update = async (ch: Channel, patch: Partial<Pick<Channel, 'role' | 'watching'>>) => {
    setChannels(prev => prev.map(c => c.chat_id === ch.chat_id ? { ...c, ...patch } : c));
    try { const r = await fetch(`/api/v1/souls/agents/${agentId}/channels/${encodeURIComponent(ch.chat_id)}`, { method: 'POST', headers, body: JSON.stringify(patch) }); if (r.ok) recount(); } catch { fetchChannels(); }
  };

  const filtered = channels.filter(c => {
    if (roleFilter !== 'all' && c.role !== roleFilter) return false;
    if (watchFilter === 'on' && !c.watching) return false;
    if (watchFilter === 'off' && c.watching) return false;
    if (query.trim() && !`${c.title || ''} ${c.username || ''} ${c.chat_id}`.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });
  const bulk = async (patch: { role?: string; watching?: boolean }) => {
    const chat_ids = filtered.map(c => c.chat_id);
    if (!chat_ids.length) return;
    setChannels(prev => prev.map(c => chat_ids.includes(c.chat_id) ? { ...c, ...patch } as Channel : c));
    try { const r = await fetch(`/api/v1/souls/agents/${agentId}/channels/bulk`, { method: 'POST', headers, body: JSON.stringify({ chat_ids, ...patch }) }); if (r.ok) apply(await r.json()); } catch { fetchChannels(); }
  };

  const columns: Col<Channel>[] = [
    { key: 'title', header: 'Канал', minWidth: 280, sortValue: c => (c.title || c.username || c.chat_id).toLowerCase(),
      render: c => <Stack gap={0}><Text fw={600} size="sm">{c.title || c.username || c.chat_id}</Text><Text size="xs" c="dimmed">{c.username ? `@${c.username}` : c.chat_id} · {c.type}{c.members ? ` · ${c.members.toLocaleString('ru-RU')} уч.` : ''}</Text></Stack> },
    { key: 'role', header: 'Роль', minWidth: 240, sortable: false,
      render: c => <SegmentedControl size="xs" value={c.role} onChange={v => update(c, { role: v as any })}
        data={ROLES.map(r => ({ label: ROLE_LABEL[r], value: r }))} /> },
    { key: 'watching', header: 'Слежение', minWidth: 110, sortValue: c => c.watching ? 1 : 0,
      render: c => <Switch checked={c.watching} onChange={() => update(c, { watching: !c.watching })} size="sm" /> },
  ];

  return (
    <DetailPage
      onBack={onClose}
      title={`Каналы аккаунта · ${label}`}
      headerRight={<Button variant="default" leftSection={<RotateCw size={15} />} loading={refreshing} onClick={refresh}>Обновить из Telegram</Button>}
      footer={<Button variant="default" onClick={onClose}>Закрыть</Button>}
    >
      {error && <Text c="red" size="sm" mb="sm">{error}</Text>}
      <Tabs defaultValue="channels">
        <Tabs.List mb="md">
          <Tabs.Tab value="channels">Каналы {channels.length ? `(${channels.length})` : ''}</Tabs.Tab>
          <Tabs.Tab value="actions">Действия бота</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="channels">
          <Group gap="md" mb="sm" justify="space-between">
            <Group gap="xs">
              <Badge color="indigo" variant="light">Цели: {counts.target ?? 0}</Badge>
              <Badge color="teal" variant="light">Новости: {counts.news ?? 0}</Badge>
              <Badge color="gray" variant="light">Игнор: {counts.ignored ?? 0}</Badge>
            </Group>
            <Text size="xs" c="dimmed">{syncedAt ? `обновлено: ${new Date(syncedAt).toLocaleString('ru-RU')}` : 'из кэша'}</Text>
          </Group>

          <Group gap="sm" mb="sm" wrap="wrap">
            <TextInput leftSection={<Search size={15} />} placeholder="Поиск канала…" value={query} onChange={e => setQuery(e.currentTarget.value)} w={260} />
            <SegmentedControl size="xs" value={roleFilter} onChange={setRoleFilter} data={[{ label: 'все роли', value: 'all' }, ...ROLES.map(r => ({ label: ROLE_LABEL[r], value: r }))]} />
            <SegmentedControl size="xs" value={watchFilter} onChange={setWatchFilter} data={[{ label: 'все', value: 'all' }, { label: '👁 слежу', value: 'on' }, { label: '⏸ пауза', value: 'off' }]} />
          </Group>

          <Paper withBorder p="xs" radius="md" mb="sm">
            <Group gap="xs">
              <Text size="xs" c="dimmed">Ко всем видимым ({filtered.length}):</Text>
              {ROLES.map(r => <Button key={r} size="xs" variant="light" color={ROLE_COLOR[r]} onClick={() => bulk({ role: r })}>→ {ROLE_LABEL[r]}</Button>)}
              <Button size="xs" variant="light" color="teal" onClick={() => bulk({ watching: true })}>👁 слежу</Button>
              <Button size="xs" variant="light" color="gray" onClick={() => bulk({ watching: false })}>⏸ пауза</Button>
            </Group>
          </Paper>

          {loading ? <Center py="xl"><Loader /></Center>
            : <DataView columns={columns} rows={filtered} rowKey={c => c.chat_id} pageSize={50}
                emptyText={channels.length === 0 ? 'Кэш пуст — нажмите «Обновить из Telegram».' : 'Ничего не найдено по фильтру.'} />}
        </Tabs.Panel>

        <Tabs.Panel value="actions">
          {actions.length === 0 ? <Text c="dimmed" size="sm">Действий пока нет.</Text> : (
            <Stack gap="xs">{actions.map(a => (
              <Paper key={a.id} withBorder p="sm" radius="md">
                <Group gap="xs" mb={2}>
                  <Text size="xs" c="dimmed">{new Date(a.created_at).toLocaleString('ru-RU')}</Text>
                  <Badge size="sm" variant="light">{a.action_type}</Badge>
                  <Badge size="sm" color={a.status === 'SUCCESS' ? 'teal' : 'orange'} variant="light">{a.status}</Badge>
                </Group>
                {a.target_url && <Anchor size="xs" href={a.target_url} target="_blank">{a.target_url}</Anchor>}
                {a.text_content && <Text size="sm" mt={2}>«{a.text_content}»</Text>}
              </Paper>))}</Stack>
          )}
        </Tabs.Panel>
      </Tabs>
    </DetailPage>
  );
};

export default ChannelManager;
