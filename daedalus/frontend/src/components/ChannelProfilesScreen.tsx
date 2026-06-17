import { useState, useEffect, useCallback } from 'react';
import { Box, Group, Stack, Title, Text, Badge, Button, Paper, SimpleGrid } from '@mantine/core';
import { Compass, RefreshCw, MapPin, Flame } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';

interface Theme { theme: string; count?: number; }
interface ChannelProfile {
  platform: string; channel_ref: string; title: string | null;
  geo_layers: string[]; geo_label: string | null; topics: string[]; tags: string[];
  recent_themes: Theme[]; summary: string | null; audience_tone: string | null; language: string | null;
  sample_count: number; posts_seen: number; last_profiled_at: string | null; last_themes_at: string | null;
}
interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; }

const LAYER_COLOR: Record<string, string> = { global: 'blue', regional: 'violet', state: 'pink', city: 'orange', personal: 'teal' };

export default function ChannelProfilesScreen({ token, selectedId, onOpen, onBack }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [profiles, setProfiles] = useState<ChannelProfile[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    try { const r = await fetch('/api/v1/channels/profiles', { headers }); if (r.ok) setProfiles((await r.json()).profiles || []); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  if (selectedId) {
    const p = profiles.find(x => x.channel_ref === selectedId);
    if (!p) return <DetailPage onBack={onBack} title="Загрузка…"><Text c="dimmed">Профиль загружается…</Text></DetailPage>;
    return <ProfileDetail profile={p} onBack={onBack} />;
  }

  const columns: Col<ChannelProfile>[] = [
    { key: 'channel', header: 'Канал', minWidth: 220, sortValue: p => (p.title || p.channel_ref).toLowerCase(),
      render: p => <Stack gap={0}><Text fw={600}>{p.title || p.channel_ref}</Text><Text size="xs" c="dimmed">{p.channel_ref}{p.language ? ` · ${p.language}` : ''}</Text></Stack> },
    { key: 'geo', header: 'Регион', minWidth: 180, sortValue: p => (p.geo_label || '').toLowerCase(),
      render: p => <Stack gap={2}>{p.geo_label && <Group gap={4}><MapPin size={13} /><Text size="sm">{p.geo_label}</Text></Group>}<Group gap={4}>{(p.geo_layers || []).map(l => <Badge key={l} size="sm" color={LAYER_COLOR[l] || 'gray'} variant="light">{l}</Badge>)}</Group></Stack> },
    { key: 'topics', header: 'Тематика', minWidth: 200, sortable: false,
      render: p => <Group gap={4}>{(p.topics || []).map(t => <Badge key={t} size="sm" variant="outline">{t}</Badge>)}</Group> },
    { key: 'themes', header: 'Сейчас обсуждают', minWidth: 220, sortable: false,
      render: p => <Group gap={4}>{(p.recent_themes || []).map(t => <Badge key={t.theme} size="sm" color="orange" variant="light" leftSection={<Flame size={10} />}>{t.theme}{t.count ? ` ·${t.count}` : ''}</Badge>)}</Group> },
    { key: 'last_profiled_at', header: 'Обновлён', minWidth: 150, sortValue: p => p.last_profiled_at || '',
      render: p => <Text size="xs" c="dimmed">{p.last_profiled_at ? new Date(p.last_profiled_at).toLocaleString('ru-RU') : '—'}</Text> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Compass size={22} style={{ verticalAlign: -4 }} /> Профили каналов</Title>
          <Text size="sm" c="dimmed">Что рой знает про каждый канал — нажмите на строку, чтобы открыть полный профиль.</Text>
        </div>
        <Button variant="default" leftSection={<RefreshCw size={15} />} onClick={fetchProfiles}>Обновить</Button>
      </Group>
      <DataView
        columns={columns} rows={profiles} rowKey={p => `${p.platform}:${p.channel_ref}`} loading={loading}
        searchText={p => `${p.title || ''} ${p.channel_ref} ${p.geo_label || ''} ${(p.topics || []).join(' ')}`}
        searchPlaceholder="🔍 Поиск по каналу, гео, теме…"
        emptyText="Профилей пока нет — строятся автоматически по целевым каналам активных миссий."
        onRowClick={p => onOpen(p.channel_ref)}
      />
    </Box>
  );
}

function ProfileDetail({ profile: p, onBack }: { profile: ChannelProfile; onBack: () => void }) {
  return (
    <DetailPage onBack={onBack} title={p.title || p.channel_ref}
      subtitle={<Text span ff="monospace" size="sm" c="dimmed">{p.channel_ref}{p.language ? ` · ${p.language}` : ''}</Text>}
      headerRight={p.geo_label ? <Badge size="lg" variant="light" leftSection={<MapPin size={13} />}>{p.geo_label}</Badge> : undefined}
      footer={<Button variant="default" onClick={onBack}>Назад</Button>}>
      <Stack gap="lg" maw={820}>
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <Paper withBorder p="sm" radius="md" ta="center"><Text size="xs" c="dimmed">постов в выборке</Text><Text fw={700}>{p.sample_count}</Text></Paper>
          <Paper withBorder p="sm" radius="md" ta="center"><Text size="xs" c="dimmed">постов всего</Text><Text fw={700}>{p.posts_seen}</Text></Paper>
          <Paper withBorder p="sm" radius="md" ta="center"><Text size="xs" c="dimmed">профиль</Text><Text size="sm">{p.last_profiled_at ? new Date(p.last_profiled_at).toLocaleDateString('ru-RU') : '—'}</Text></Paper>
          <Paper withBorder p="sm" radius="md" ta="center"><Text size="xs" c="dimmed">темы</Text><Text size="sm">{p.last_themes_at ? new Date(p.last_themes_at).toLocaleTimeString('ru-RU') : '—'}</Text></Paper>
        </SimpleGrid>

        <Box><Text fw={600} mb={6}>Гео</Text><Group gap={6}>{p.geo_label && <Badge variant="light" leftSection={<MapPin size={12} />}>{p.geo_label}</Badge>}{(p.geo_layers || []).map(l => <Badge key={l} color={LAYER_COLOR[l] || 'gray'} variant="light">{l}</Badge>)}</Group></Box>
        <Box><Text fw={600} mb={6}>Тематика</Text><Group gap={6}>{(p.topics || []).map(t => <Badge key={t} variant="outline">{t}</Badge>)}{(p.tags || []).map(t => <Badge key={t} variant="dot">#{t}</Badge>)}</Group></Box>
        <Box><Text fw={600} mb={6}>Сейчас обсуждают</Text>{(p.recent_themes || []).length === 0 ? <Text c="dimmed" size="sm">—</Text> : <Group gap={6}>{p.recent_themes.map(t => <Badge key={t.theme} color="orange" variant="light" leftSection={<Flame size={11} />}>{t.theme}{t.count ? ` ·${t.count}` : ''}</Badge>)}</Group>}</Box>
        {p.summary && <Paper withBorder p="md" radius="md"><Text fw={600} mb={4}>Чем является</Text><Text>{p.summary}</Text>{p.audience_tone && <Text size="sm" c="dimmed" mt={6}>Тон аудитории: {p.audience_tone}</Text>}</Paper>}
      </Stack>
    </DetailPage>
  );
}
