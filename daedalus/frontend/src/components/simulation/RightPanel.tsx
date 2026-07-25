/**
 * Right column — channels on top (list / search / create / edit), actions and the
 * state inspector at the bottom: author pickers, manual comment field, mass
 * generation, mission creation, knowledge import and landscape scraping.
 */
import { useEffect, useState } from 'react';
import {
  ActionIcon, Avatar, Badge, Box, Button, Code, Divider, Group, Paper, ScrollArea, Select,
  SimpleGrid, Stack, Text, TextInput, Textarea, Tooltip,
} from '@mantine/core';
import {
  Bot, Brain, Globe, MessageSquarePlus, Plus, Search, Send, Sparkles, Target, User, Eye, Pencil,
} from 'lucide-react';
import {
  SimAccount, SimApi, SimChannel, SimMission, SimPersona, SimPost, initialsOf,
} from './api';

export interface InspectTarget { entity: string; id: number; label: string }

interface Props {
  api: SimApi;
  channels: SimChannel[];
  accounts: SimAccount[];
  personas: SimPersona[];
  missions: SimMission[];
  selectedChannelId: number | null;
  activePost: SimPost | null;
  inspect: InspectTarget | null;
  onSelectChannel: (id: number) => void;
  onCreateChannel: () => void;
  onEditChannel: (c: SimChannel) => void;
  onCreateAccount: () => void;
  onEditAccount: (a: SimAccount) => void;
  onCreatePersona: () => void;
  onEditPersona: (p: SimPersona) => void;
  onOpenMissions: () => void;
  onCreateMission: () => void;
  onNewPost: () => void;
  onMassGen: () => void;
  onKnowledge: () => void;
  onLandscape: () => void;
  onQuickComment: (payload: { authorKind: 'account' | 'persona'; id: number; text: string }) => void;
  onInspect: (t: InspectTarget) => void;
}

export default function RightPanel(props: Props) {
  const {
    api, channels, accounts, personas, missions, selectedChannelId, activePost, inspect,
    onSelectChannel, onCreateChannel, onEditChannel, onCreateAccount, onEditAccount,
    onCreatePersona, onEditPersona, onOpenMissions, onCreateMission, onNewPost, onMassGen,
    onKnowledge, onLandscape, onQuickComment, onInspect,
  } = props;

  const [query, setQuery] = useState('');
  const [quickKind, setQuickKind] = useState<'account' | 'persona'>('account');
  const [quickId, setQuickId] = useState<string | null>(null);
  const [quickText, setQuickText] = useState('');
  const [inspectData, setInspectData] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    if (!inspect) { setInspectData(null); return; }
    let alive = true;
    api.inspect(inspect.entity, inspect.id)
      .then(r => { if (alive) setInspectData(r.data); })
      .catch(() => { if (alive) setInspectData(null); });
    return () => { alive = false; };
  }, [inspect, api]);

  const visible = channels.filter(c =>
    !query.trim() ||
    `${c.username} ${c.title} ${(c.tags || []).join(' ')}`.toLowerCase().includes(query.toLowerCase()));

  const sendQuick = () => {
    if (!quickId || !quickText.trim()) return;
    onQuickComment({ authorKind: quickKind, id: Number(quickId), text: quickText.trim() });
    setQuickText('');
  };

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* ── Channels ── */}
      <Group justify="space-between" p="sm" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <Text fw={600} size="sm">Каналы ({channels.length})</Text>
        <Button size="compact-xs" leftSection={<Plus size={13} />} onClick={onCreateChannel}>Создать</Button>
      </Group>
      <Box px="sm" pt="xs">
        <TextInput size="xs" placeholder="Поиск по каналам…" leftSection={<Search size={13} />}
          value={query} onChange={e => setQuery(e.currentTarget.value)} />
      </Box>
      <ScrollArea style={{ flex: '0 0 auto', maxHeight: 230 }} type="hover">
        <Stack gap={4} p="xs">
          {visible.length === 0 && <Text size="xs" c="dimmed" ta="center" py="sm">Каналов нет.</Text>}
          {visible.map(c => (
            <Paper key={c.id} withBorder={c.id === selectedChannelId} p={6} radius="sm"
              style={{
                cursor: 'pointer',
                background: c.id === selectedChannelId ? 'var(--bg-card-hover)' : 'transparent',
              }}
              onClick={() => onSelectChannel(c.id)}>
              <Group gap="xs" wrap="nowrap">
                <Avatar size="sm" radius="xl" color={c.avatar_color}>{initialsOf(c.title)}</Avatar>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Text size="xs" fw={600} truncate>{c.title}</Text>
                  <Text size="xs" c="dimmed" truncate>{c.username} · {c.posts_count} постов</Text>
                </div>
                <Group gap={2} wrap="nowrap">
                  <Tooltip label="Изменить канал">
                    <ActionIcon size="sm" variant="subtle" onClick={e => { e.stopPropagation(); onEditChannel(c); }}>
                      <Pencil size={12} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Состояние">
                    <ActionIcon size="sm" variant="subtle" onClick={e => {
                      e.stopPropagation(); onInspect({ entity: 'channel', id: c.id, label: c.username });
                    }}><Eye size={12} /></ActionIcon>
                  </Tooltip>
                </Group>
              </Group>
            </Paper>
          ))}
        </Stack>
      </ScrollArea>

      <Divider />

      <ScrollArea style={{ flex: 1 }} type="hover">
        <Stack gap="sm" p="sm">
          {/* ── Actions ── */}
          <Text fw={600} size="sm">Действия</Text>
          {/* Two-up for the short labels; the long ones get their own full-width row
              so nothing is ever truncated in this narrow column. */}
          <SimpleGrid cols={2} spacing={6}>
            <Button size="xs" variant="light" leftSection={<MessageSquarePlus size={14} />}
              onClick={onNewPost} disabled={!selectedChannelId}>Новый пост</Button>
            <Button size="xs" variant="light" leftSection={<Target size={14} />}
              onClick={onCreateMission}>Новая миссия</Button>
            <Button size="xs" variant="light" leftSection={<Target size={14} />}
              onClick={onOpenMissions}>Все миссии ({missions.length})</Button>
          </SimpleGrid>
          <Button fullWidth size="xs" variant="light" leftSection={<Sparkles size={14} />}
            onClick={onMassGen} disabled={!activePost}>Массовая генерация</Button>
          <Button fullWidth size="xs" variant="light" leftSection={<Brain size={14} />}
            onClick={onKnowledge}>Импорт знаний</Button>
          <Button fullWidth size="xs" variant="light" leftSection={<Globe size={14} />}
            onClick={onLandscape}>Ландшафт-скрапинг</Button>
          {!activePost && (
            <Text size="xs" c="dimmed">Массовая генерация включается, когда открыт пост.</Text>
          )}

          {/* ── Quick manual comment ── */}
          <Divider label="Быстрый комментарий" labelPosition="left" />
          {!activePost ? (
            <Text size="xs" c="dimmed">Откройте пост, чтобы писать в его обсуждение.</Text>
          ) : (
            <Stack gap={6}>
              <Group gap={6} grow>
                <Select size="xs" data={[
                  { value: 'account', label: 'Аккаунт' }, { value: 'persona', label: 'Агент' }]}
                  value={quickKind} onChange={v => { setQuickKind(v as any); setQuickId(null); }} />
                <Select size="xs" placeholder="кто" searchable
                  data={(quickKind === 'account'
                    ? accounts.map(a => ({ value: String(a.id), label: a.display_name }))
                    : personas.map(p => ({ value: String(p.id), label: p.codename })))}
                  value={quickId} onChange={setQuickId} />
              </Group>
              <Textarea size="xs" autosize minRows={2} placeholder="Текст комментария…"
                value={quickText} onChange={e => setQuickText(e.currentTarget.value)} />
              <Button size="xs" leftSection={<Send size={13} />} onClick={sendQuick}
                disabled={!quickId || !quickText.trim()}>Отправить в пост #{activePost.id}</Button>
            </Stack>
          )}

          {/* ── Executors ── */}
          <Divider label="Исполнители" labelPosition="left" />
          <Group justify="space-between">
            <Group gap={4}><User size={13} /><Text size="xs" fw={600}>Аккаунты (ручные)</Text></Group>
            <ActionIcon size="sm" variant="subtle" onClick={onCreateAccount}><Plus size={13} /></ActionIcon>
          </Group>
          <Stack gap={3}>
            {accounts.length === 0 && <Text size="xs" c="dimmed">Аккаунтов нет.</Text>}
            {accounts.map(a => (
              <Group key={a.id} gap={6} wrap="nowrap" style={{ cursor: 'pointer' }}
                onClick={() => onEditAccount(a)}>
                <Avatar size={20} radius="xl" color={a.color}>{a.initials || initialsOf(a.display_name)}</Avatar>
                <Text size="xs" truncate style={{ flex: 1 }}>{a.display_name} <Text span c="dimmed">{a.handle}</Text></Text>
                {a.status !== 'active' && <Badge size="xs" color="gray">{a.status}</Badge>}
                <ActionIcon size="xs" variant="subtle" onClick={e => {
                  e.stopPropagation(); onInspect({ entity: 'account', id: a.id, label: a.handle });
                }}><Eye size={11} /></ActionIcon>
              </Group>
            ))}
          </Stack>

          <Group justify="space-between" mt={4}>
            <Group gap={4}><Bot size={13} /><Text size="xs" fw={600}>Агенты (ИИ)</Text></Group>
            <ActionIcon size="sm" variant="subtle" onClick={onCreatePersona}><Plus size={13} /></ActionIcon>
          </Group>
          <Stack gap={3}>
            {personas.length === 0 && <Text size="xs" c="dimmed">Агентов нет.</Text>}
            {personas.map(p => (
              <Group key={p.id} gap={6} wrap="nowrap" style={{ cursor: 'pointer' }}
                onClick={() => onEditPersona(p)}>
                <Avatar size={20} radius="xl" color={p.color}>{initialsOf(p.codename)}</Avatar>
                <Text size="xs" truncate style={{ flex: 1 }}>{p.codename}</Text>
                <Badge size="xs" variant="light">{p.caste}</Badge>
                <ActionIcon size="xs" variant="subtle" onClick={e => {
                  e.stopPropagation(); onInspect({ entity: 'persona', id: p.id, label: p.codename });
                }}><Eye size={11} /></ActionIcon>
              </Group>
            ))}
          </Stack>

          {/* ── Inspector ── */}
          <Divider label="Инспектор состояния" labelPosition="left" />
          {!inspect && <Text size="xs" c="dimmed">Нажмите «глаз» у любой сущности, чтобы увидеть её состояние.</Text>}
          {inspect && (
            <Stack gap={4}>
              <Group gap={6}>
                <Badge size="sm" variant="light">{inspect.entity}</Badge>
                <Text size="xs" c="dimmed">{inspect.label}</Text>
              </Group>
              <ScrollArea h={200}>
                <Code block style={{ fontSize: 10, whiteSpace: 'pre-wrap' }}>
                  {inspectData ? JSON.stringify(inspectData, null, 2) : 'загрузка…'}
                </Code>
              </ScrollArea>
            </Stack>
          )}
        </Stack>
      </ScrollArea>
    </Box>
  );
}
