import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, ActionIcon, Tabs, TextInput, NumberInput,
  Select, SegmentedControl, Slider, Chip, Textarea, Table, Tooltip, Paper, ScrollArea,
} from '@mantine/core';
import {
  Shield, Play, Pause, Trash2, Plus, X, Link as LinkIcon, Unlink, History, RotateCcw, Radio,
  FlaskConical,
} from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';
import { EntityPicker } from '../ui/EntityPicker';
import ChannelManager from './ChannelManager';

interface CommunicationStyle { tone_level: number; vocab_level: number; emoji_frequency: number; aggression: number; quirks: string[]; }
interface BehavioralRules { rules: string[]; min_delay_between_posts_sec: number; max_posts_per_hour: number; }
interface Account { id: number; agent_id: string | null; platform: string; username: string; status: string; device_id: string | null; }
interface Profile {
  id: number; agent_id: string; codename: string; full_name: string; caste: string; status: string;
  profession: string | null; residence_city: string | null; platforms: string[];
  active_hours_start: number; active_hours_end: number;
  communication_style: CommunicationStyle | null; behavioral_rules: BehavioralRules | Record<string, any> | null;
  core_mission: string | null; current_stance_modifiers: Record<string, string> | null;
  context_subscriptions: string[] | null;
}
interface StancePair { topic: string; stance: string; }
interface LiveStatus { agent_id: string; event: string; detail: string; status: string; ts: string; }

interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; goTo?: (view: string, id?: string) => void; }

const CASTES = ['alpha', 'beta', 'gamma'];
const PLATFORMS = ['telegram', 'instagram', 'youtube', 'threads', 'web'];
const SUBSCRIPTION_LAYERS = ['global', 'regional', 'state', 'city', 'personal'];
const CASTE_COLOR: Record<string, string> = { alpha: 'red', beta: 'blue', gamma: 'green' };
const STATUS_COLOR: Record<string, string> = { active: 'teal', suspended: 'orange', unbound: 'gray', banned: 'red', limited: 'yellow' };
const STATUS_LABEL: Record<string, string> = { active: 'активен', suspended: 'на паузе', unbound: 'без аккаунта', banned: 'забанен', limited: 'ограничен' };

const normalizeComm = (raw: any): CommunicationStyle => ({
  tone_level: Number(raw?.tone_level ?? 5), vocab_level: Number(raw?.vocab_level ?? 5),
  emoji_frequency: Number(raw?.emoji_frequency ?? 3), aggression: Number(raw?.aggression ?? 3),
  quirks: Array.isArray(raw?.quirks) ? raw.quirks.map(String) : [],
});
const normalizeRules = (raw: any): BehavioralRules => ({
  rules: Array.isArray(raw?.rules) ? raw.rules.map(String) : [],
  min_delay_between_posts_sec: Number(raw?.min_delay_between_posts_sec ?? 45),
  max_posts_per_hour: Number(raw?.max_posts_per_hour ?? 5),
});
function liveness(s: LiveStatus | undefined): 'working' | 'idle' | 'asleep' {
  if (!s) return 'asleep';
  const ageMs = Date.now() - parseFloat(s.ts) * 1000;
  if (s.status === 'active' && ageMs < 90_000) return 'working';
  if (ageMs < 300_000) return 'idle';
  return 'asleep';
}
const LIVE_COLOR = { working: '#34c98b', idle: '#e0a23c', asleep: '#475569' } as const;

export default function SoulsScreen({ token, selectedId, onOpen, onBack, goTo }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [live, setLive] = useState<Record<string, LiveStatus>>({});
  const [loading, setLoading] = useState(true);
  const [casteFilter, setCasteFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers });
      if (res.ok) setProfiles(await res.json());
    } finally { setLoading(false); }
  }, [token]);
  const fetchAccounts = useCallback(async () => {
    const res = await fetch('/api/v1/souls/accounts', { headers });
    if (res.ok) setAccounts(await res.json());
  }, [token]);
  const fetchLive = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/analytics/live?limit=1', { headers });
      if (!res.ok) return;
      const data = await res.json();
      const map: Record<string, LiveStatus> = {};
      for (const a of data.agents || []) map[a.agent_id] = a;
      setLive(map);
    } catch { /* best-effort */ }
  }, [token]);

  useEffect(() => {
    fetchProfiles(); fetchAccounts(); fetchLive();
    const iv = setInterval(fetchLive, 4000);
    return () => clearInterval(iv);
  }, [fetchProfiles, fetchAccounts, fetchLive]);

  const setAgentStatus = async (agentId: string, status: 'active' | 'suspended') => {
    const res = await fetch(`/api/v1/souls/profiles/${agentId}/status`, { method: 'POST', headers, body: JSON.stringify({ status }) });
    if (res.ok) setProfiles(prev => prev.map(p => p.agent_id === agentId ? { ...p, status } : p));
  };

  const selected = selectedId ? profiles.find(p => p.agent_id === selectedId) || null : null;

  // ── Detail view ──
  if (selectedId) {
    if (!selected) {
      return <DetailPage onBack={onBack} title="Загрузка…" subtitle={selectedId}><Text c="dimmed">Профиль загружается…</Text></DetailPage>;
    }
    return (
      <SoulDetail
        key={selected.agent_id}
        token={token} profile={selected} accounts={accounts}
        onBack={onBack}
        onSaved={() => fetchProfiles()}
        onAccountsChanged={() => { fetchAccounts(); fetchProfiles(); }}
        onStatus={setAgentStatus}
        onDeleted={() => { fetchProfiles(); onBack(); }}
        goTo={goTo}
      />
    );
  }

  // ── List view ──
  const boundFor = (agentId: string) => accounts.filter(a => a.agent_id === agentId);
  const columns: Col<Profile>[] = [
    { key: 'live', header: '', sortable: false, width: 28, minWidth: 28,
      render: p => { const lv = liveness(live[p.agent_id]); return (
        <Tooltip label={lv === 'working' ? 'работает сейчас' : lv === 'idle' ? 'недавно активен' : 'не активен'}>
          <Box style={{ width: 10, height: 10, borderRadius: 999, background: LIVE_COLOR[lv] }} />
        </Tooltip>); } },
    { key: 'full_name', header: 'Агент', minWidth: 220, sortValue: p => (p.full_name || p.agent_id).toLowerCase(),
      render: p => (<Stack gap={0}><Text fw={600}>{p.full_name || p.agent_id}</Text>
        <Text size="xs" c="dimmed" ff="monospace">{p.agent_id}</Text></Stack>) },
    { key: 'caste', header: 'Каста', minWidth: 90, sortValue: p => p.caste,
      render: p => <Badge color={CASTE_COLOR[p.caste] || 'gray'} variant="light">{p.caste}</Badge> },
    { key: 'status', header: 'Статус', minWidth: 120, sortValue: p => p.status,
      render: p => <Badge color={STATUS_COLOR[p.status] || 'gray'} variant="light">{STATUS_LABEL[p.status] || p.status}</Badge> },
    { key: 'profession', header: 'Профессия', minWidth: 160, render: p => p.profession || '—' },
    { key: 'accounts', header: 'Аккаунты', minWidth: 200, sortable: false,
      render: p => { const b = boundFor(p.agent_id); return b.length === 0
        ? <Text size="xs" c="dimmed">не привязан</Text>
        : <Group gap={4}>{b.map(a => <Badge key={a.id} variant="outline" size="sm">{a.platform}:{a.username}</Badge>)}</Group>; } },
    { key: 'actions', header: '', sortable: false, minWidth: 130, align: 'right',
      render: p => (
        <Group gap={6} justify="flex-end" onClick={e => e.stopPropagation()}>
          {p.status === 'suspended'
            ? <Button size="xs" variant="light" color="teal" leftSection={<Play size={13} />} onClick={() => setAgentStatus(p.agent_id, 'active')}>Запуск</Button>
            : <Button size="xs" variant="light" color="orange" leftSection={<Pause size={13} />} onClick={() => setAgentStatus(p.agent_id, 'suspended')}>Пауза</Button>}
        </Group>) },
  ];

  const shown = profiles.filter(p =>
    (casteFilter === 'all' || p.caste === casteFilter) &&
    (statusFilter === 'all' || p.status === statusFilter));

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Shield size={22} style={{ verticalAlign: -4 }} /> Души (хранилище)</Title>
          <Text size="sm" c="dimmed">Персоны роя — нажмите на строку, чтобы открыть полный профиль и отредактировать все параметры.</Text>
        </div>
      </Group>
      <DataView
        columns={columns}
        rows={shown}
        rowKey={p => p.agent_id}
        loading={loading}
        searchText={p => `${p.full_name} ${p.codename} ${p.agent_id} ${p.profession || ''}`}
        searchPlaceholder="🔍 Поиск по имени, codename, agent_id…"
        emptyText="Профили не найдены."
        onRowClick={p => onOpen(p.agent_id)}
        toolbar={
          <Group gap="sm">
            <SegmentedControl size="xs" value={casteFilter} onChange={setCasteFilter}
              data={[{ label: 'все касты', value: 'all' }, ...CASTES.map(c => ({ label: c, value: c }))]} />
            <SegmentedControl size="xs" value={statusFilter} onChange={setStatusFilter}
              data={[{ label: 'все', value: 'all' }, { label: 'активные', value: 'active' }, { label: 'пауза', value: 'suspended' }]} />
          </Group>
        }
      />
    </Box>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
function SoulDetail({ token, profile, accounts, onBack, onSaved, onAccountsChanged, onStatus, onDeleted, goTo }: {
  token: string; profile: Profile; accounts: Account[];
  onBack: () => void; onSaved: () => void; onAccountsChanged: () => void;
  onStatus: (id: string, s: 'active' | 'suspended') => void; onDeleted: () => void; goTo?: (view: string, id?: string) => void;
}) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [p, setP] = useState<Profile>(() => ({
    ...profile,
    communication_style: normalizeComm(profile.communication_style),
    behavioral_rules: normalizeRules(profile.behavioral_rules),
    context_subscriptions: Array.isArray(profile.context_subscriptions) ? profile.context_subscriptions : ['global'],
  }));
  const [stancePairs, setStancePairs] = useState<StancePair[]>(
    Object.entries(profile.current_stance_modifiers || {}).map(([topic, s]) => ({ topic, stance: String(s) })));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [history, setHistory] = useState<any[]>([]);
  const [pickAccount, setPickAccount] = useState(false);
  const [channelMgr, setChannelMgr] = useState(false);
  // Simulation → production: pick a persona that was tuned on the polygon and
  // prefill this soul's fields with it (nothing is saved until "Сохранить").
  const [simPersonas, setSimPersonas] = useState<any[]>([]);
  const [pickSim, setPickSim] = useState(false);

  const comm = p.communication_style as CommunicationStyle;
  const rules = p.behavioral_rules as BehavioralRules;
  const setComm = (k: keyof CommunicationStyle, v: any) => setP(s => ({ ...s, communication_style: { ...(s.communication_style as CommunicationStyle), [k]: v } }));
  const setRules = (k: keyof BehavioralRules, v: any) => setP(s => ({ ...s, behavioral_rules: { ...(s.behavioral_rules as BehavioralRules), [k]: v } }));

  const fetchHistory = useCallback(async () => {
    const res = await fetch(`/api/v1/souls/profiles/${p.agent_id}/history`, { headers });
    if (res.ok) setHistory(await res.json());
  }, [p.agent_id, token]);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  const openSimPicker = async () => {
    try {
      const res = await fetch('/api/v1/simulation/personas', { headers });
      if (res.ok) setSimPersonas((await res.json()).personas || []);
    } catch { setSimPersonas([]); }
    setPickSim(true);
  };

  /** Copy a полигон-tested persona into this (production) soul's form. */
  const applySimPersona = async (personaId: number) => {
    const res = await fetch(`/api/v1/simulation/personas/${personaId}/export`, { headers });
    if (!res.ok) return;
    const s = await res.json();
    setP(prev => ({
      ...prev,
      codename: s.codename || prev.codename,
      full_name: s.full_name || prev.full_name,
      caste: s.caste || prev.caste,
      profession: s.bio || prev.profession,
      core_mission: s.core_mission ?? prev.core_mission,
      communication_style: normalizeComm(s.communication_style),
      behavioral_rules: normalizeRules({ ...normalizeRules(prev.behavioral_rules), ...(s.behavioral_rules || {}) }),
    }));
  };

  const boundAccounts = useMemo(() => accounts.filter(a => a.agent_id === p.agent_id), [accounts, p.agent_id]);
  const freeAccounts = useMemo(() => accounts.filter(a => !a.agent_id), [accounts]);

  const save = async () => {
    setSaving(true); setSaveError('');
    const stanceDict: Record<string, string> = {};
    for (const pair of stancePairs) { const t = pair.topic.trim(); if (t) stanceDict[t] = pair.stance; }
    const payload = {
      codename: p.codename, full_name: p.full_name, caste: p.caste, profession: p.profession,
      residence_city: p.residence_city, platforms: p.platforms,
      active_hours_start: p.active_hours_start, active_hours_end: p.active_hours_end,
      communication_style: comm, behavioral_rules: rules, core_mission: p.core_mission,
      current_stance_modifiers: stanceDict, context_subscriptions: p.context_subscriptions || ['global'],
    };
    try {
      const res = await fetch(`/api/v1/souls/profiles/${p.agent_id}`, { method: 'PUT', headers, body: JSON.stringify(payload) });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(typeof b.detail === 'string' ? b.detail : `HTTP ${res.status}`); }
      onSaved();
    } catch (e) { setSaveError(e instanceof Error ? e.message : 'Не удалось сохранить'); }
    finally { setSaving(false); }
  };

  const bindAccount = async (acc: Account) => {
    const res = await fetch(`/api/v1/souls/accounts/${acc.id}/bind?agent_id=${encodeURIComponent(p.agent_id)}`, { method: 'PUT', headers });
    if (res.ok) onAccountsChanged();
  };
  const unbindAccount = async (acc: Account) => {
    const res = await fetch(`/api/v1/souls/accounts/${acc.id}/unbind`, { method: 'PUT', headers });
    if (res.ok) onAccountsChanged();
  };
  const rollback = async (historyId: number) => {
    if (!confirm('Откатить профиль к этой версии?')) return;
    const res = await fetch(`/api/v1/souls/profiles/${p.agent_id}/rollback/${historyId}`, { method: 'POST', headers });
    if (res.ok) { onSaved(); onBack(); }
  };
  const remove = async () => {
    if (!confirm(`Удалить душу «${p.full_name || p.agent_id}»?`)) return;
    const res = await fetch(`/api/v1/souls/profiles/${p.agent_id}`, { method: 'DELETE', headers });
    if (res.ok) onDeleted();
  };

  const toggleArr = (arr: string[], v: string) => arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v];
  const isTg = boundAccounts.some(a => a.platform === 'telegram');

  const slider = (label: string, key: keyof CommunicationStyle, lo: string, hi: string) => (
    <Box>
      <Group justify="space-between" mb={2}>
        <Text size="sm">{label}</Text>
        <NumberInput size="xs" w={64} min={1} max={10} value={comm[key] as number}
          onChange={v => setComm(key, Math.max(1, Math.min(10, Number(v) || 1)))} />
      </Group>
      <Slider min={1} max={10} value={comm[key] as number} onChange={v => setComm(key, v)}
        marks={[{ value: 1 }, { value: 5 }, { value: 10 }]} label={null} />
      <Group justify="space-between"><Text size="xs" c="dimmed">{lo}</Text><Text size="xs" c="dimmed">{hi}</Text></Group>
    </Box>
  );

  if (channelMgr) return <ChannelManager token={token} agentId={p.agent_id} label={p.full_name || p.agent_id} onClose={() => setChannelMgr(false)} />;

  return (
    <DetailPage
      onBack={onBack}
      title={p.full_name || p.agent_id}
      subtitle={<Text span ff="monospace" size="sm" c="dimmed">{p.agent_id} · {p.codename}</Text>}
      headerRight={
        <>
          <Badge size="lg" color={STATUS_COLOR[p.status] || 'gray'} variant="light">{STATUS_LABEL[p.status] || p.status}</Badge>
          {p.status === 'suspended'
            ? <Button variant="light" color="teal" leftSection={<Play size={15} />} onClick={() => onStatus(p.agent_id, 'active')}>Запустить</Button>
            : <Button variant="light" color="orange" leftSection={<Pause size={15} />} onClick={() => onStatus(p.agent_id, 'suspended')}>Пауза</Button>}
          {isTg && <Button variant="default" leftSection={<Radio size={15} />} onClick={() => setChannelMgr(true)}>Каналы</Button>}
          <Tooltip label="Подставить личность, отлаженную в Симуляции">
            <Button variant="default" leftSection={<FlaskConical size={15} />} onClick={openSimPicker}>Из симуляции</Button>
          </Tooltip>
        </>
      }
      footer={
        <>
          {saveError && <Text c="red" size="sm" mr="auto">{saveError}</Text>}
          <Button variant="subtle" color="red" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить душу</Button>
          <Button variant="default" onClick={onBack}>Назад</Button>
          <Button loading={saving} onClick={save}>Сохранить</Button>
        </>
      }
    >
      <Tabs defaultValue="identity" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="identity">Личность</Tabs.Tab>
          <Tabs.Tab value="psychology">Психология и стиль</Tabs.Tab>
          <Tabs.Tab value="mission">Миссия и позиция</Tabs.Tab>
          <Tabs.Tab value="accounts">Аккаунты</Tabs.Tab>
          <Tabs.Tab value="history">История</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="identity">
          <Stack gap="md" maw={720}>
            <Group grow>
              <TextInput label="Полное имя" value={p.full_name || ''} onChange={e => setP(s => ({ ...s, full_name: e.currentTarget.value }))} />
              <TextInput label="Codename" value={p.codename} onChange={e => setP(s => ({ ...s, codename: e.currentTarget.value }))} />
            </Group>
            <Group grow>
              <TextInput label="Город проживания" value={p.residence_city || ''} onChange={e => setP(s => ({ ...s, residence_city: e.currentTarget.value }))} />
              <TextInput label="Профессия" value={p.profession || ''} onChange={e => setP(s => ({ ...s, profession: e.currentTarget.value }))} />
            </Group>
            <Select label="Каста" data={CASTES} value={p.caste} onChange={v => v && setP(s => ({ ...s, caste: v }))} w={200} />
            <Box>
              <Text size="sm" mb={6}>Платформы</Text>
              <Group gap="xs">
                {PLATFORMS.map(pl => (
                  <Chip key={pl} checked={(p.platforms || []).includes(pl)} onChange={() => setP(s => ({ ...s, platforms: toggleArr(s.platforms || [], pl) }))}>{pl}</Chip>
                ))}
              </Group>
            </Box>
            <Group grow maw={360}>
              <NumberInput label="Активность с (0–23)" min={0} max={23} value={p.active_hours_start} onChange={v => setP(s => ({ ...s, active_hours_start: Number(v) || 0 }))} />
              <NumberInput label="Активность до (0–23)" min={0} max={23} value={p.active_hours_end} onChange={v => setP(s => ({ ...s, active_hours_end: Number(v) || 0 }))} />
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="psychology">
          <Stack gap="lg" maw={760}>
            <Paper withBorder p="md" radius="md">
              <Text fw={600} mb="sm">Стиль общения (1–10)</Text>
              <Group grow align="flex-start">
                <Stack gap="md">{slider('Тон', 'tone_level', 'формальный', 'неформальный')}{slider('Словарь', 'vocab_level', 'простой', 'эрудит')}</Stack>
                <Stack gap="md">{slider('Частота эмодзи', 'emoji_frequency', 'нет', 'много')}{slider('Агрессивность', 'aggression', 'пассивный', 'задиристый')}</Stack>
              </Group>
            </Paper>
            <Paper withBorder p="md" radius="md">
              <Group justify="space-between" mb="sm"><Text fw={600}>Речевые причуды</Text>
                <Button size="xs" variant="light" leftSection={<Plus size={13} />} onClick={() => setComm('quirks', [...comm.quirks, ''])}>Добавить</Button></Group>
              {comm.quirks.length === 0 && <Text c="dimmed" size="sm">Не заданы.</Text>}
              <Stack gap="xs">{comm.quirks.map((q, i) => (
                <Group key={i} gap="xs"><TextInput style={{ flex: 1 }} placeholder="напр. только строчные буквы" value={q}
                  onChange={e => { const n = [...comm.quirks]; n[i] = e.currentTarget.value; setComm('quirks', n); }} />
                  <ActionIcon variant="subtle" color="red" onClick={() => setComm('quirks', comm.quirks.filter((_, j) => j !== i))}><X size={15} /></ActionIcon></Group>))}</Stack>
            </Paper>
            <Paper withBorder p="md" radius="md">
              <Group justify="space-between" mb="sm"><Text fw={600}>Правила поведения</Text>
                <Button size="xs" variant="light" leftSection={<Plus size={13} />} onClick={() => setRules('rules', [...rules.rules, ''])}>Добавить</Button></Group>
              {rules.rules.length === 0 && <Text c="dimmed" size="sm">Не заданы.</Text>}
              <Stack gap="xs" mb="md">{rules.rules.map((r, i) => (
                <Group key={i} gap="xs"><TextInput style={{ flex: 1 }} placeholder="напр. никогда не обсуждать политику напрямую" value={r}
                  onChange={e => { const n = [...rules.rules]; n[i] = e.currentTarget.value; setRules('rules', n); }} />
                  <ActionIcon variant="subtle" color="red" onClick={() => setRules('rules', rules.rules.filter((_, j) => j !== i))}><X size={15} /></ActionIcon></Group>))}</Stack>
              <Group grow maw={420}>
                <NumberInput label="Мин. пауза между постами (сек)" min={0} value={rules.min_delay_between_posts_sec} onChange={v => setRules('min_delay_between_posts_sec', Number(v) || 0)} />
                <NumberInput label="Макс. постов в час" min={0} value={rules.max_posts_per_hour} onChange={v => setRules('max_posts_per_hour', Number(v) || 0)} />
              </Group>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="mission">
          <Stack gap="lg" maw={760}>
            <Box>
              <Text fw={600} mb={4}>Подписки на контекст (RAG)</Text>
              <Text size="xs" c="dimmed" mb="xs">Слои знаний, из которых агент берёт факты при ответе.</Text>
              <Group gap="xs">{SUBSCRIPTION_LAYERS.map(l => (
                <Chip key={l} checked={(p.context_subscriptions || []).includes(l)} onChange={() => setP(s => ({ ...s, context_subscriptions: toggleArr(s.context_subscriptions || ['global'], l) }))}>{l}</Chip>))}</Group>
            </Box>
            <Textarea label="Главная миссия" autosize minRows={4} value={p.core_mission || ''}
              onChange={e => setP(s => ({ ...s, core_mission: e.currentTarget.value }))}
              placeholder="Основная цель и нарративная позиция персоны (свободный текст, не JSON)." />
            <Paper withBorder p="md" radius="md">
              <Group justify="space-between" mb="sm"><Text fw={600}>Модификаторы позиции</Text>
                <Button size="xs" variant="light" leftSection={<Plus size={13} />} onClick={() => setStancePairs([...stancePairs, { topic: '', stance: 'support' }])}>Добавить</Button></Group>
              {stancePairs.length === 0 && <Text c="dimmed" size="sm">Не заданы.</Text>}
              <Stack gap="xs">{stancePairs.map((pair, i) => (
                <Group key={i} gap="xs">
                  <TextInput style={{ flex: 1 }} placeholder="Тема (напр. местные выборы)" value={pair.topic}
                    onChange={e => { const n = [...stancePairs]; n[i] = { ...n[i], topic: e.currentTarget.value }; setStancePairs(n); }} />
                  <Select w={160} value={pair.stance} onChange={v => { const n = [...stancePairs]; n[i] = { ...n[i], stance: v || 'support' }; setStancePairs(n); }}
                    data={[{ value: 'support', label: 'поддержка' }, { value: 'attack', label: 'атака' }, { value: 'neutral', label: 'нейтрально' }, { value: 'amplify', label: 'усиление' }, { value: 'undermine', label: 'подрыв' }]} />
                  <ActionIcon variant="subtle" color="red" onClick={() => setStancePairs(stancePairs.filter((_, j) => j !== i))}><X size={15} /></ActionIcon>
                </Group>))}</Stack>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="accounts">
          <Stack gap="md" maw={680}>
            <Group justify="space-between">
              <Text fw={600}>Привязанные аккаунты</Text>
              <Button size="xs" variant="light" leftSection={<LinkIcon size={14} />} onClick={() => setPickAccount(true)} disabled={freeAccounts.length === 0}>Привязать аккаунт</Button>
            </Group>
            {boundAccounts.length === 0 ? <Text c="dimmed" size="sm">К этой персоне аккаунты не привязаны.</Text> : (
              <Stack gap="xs">{boundAccounts.map(a => (
                <Paper key={a.id} withBorder p="sm" radius="md">
                  <Group justify="space-between">
                    <Group gap="xs"><Badge variant="outline">{a.platform}</Badge><Text>{a.username}</Text><Badge size="sm" color={STATUS_COLOR[a.status] || 'gray'} variant="light">{a.status}</Badge></Group>
                    <Group gap="xs">
                      {goTo && <Button size="xs" variant="subtle" onClick={() => goTo('accounts', String(a.id))}>открыть →</Button>}
                      <Button size="xs" variant="subtle" color="red" leftSection={<Unlink size={13} />} onClick={() => unbindAccount(a)}>Отвязать</Button>
                    </Group>
                  </Group>
                </Paper>))}</Stack>
            )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="history">
          <Stack gap="sm" maw={760}>
            <Group gap="xs"><History size={16} /><Text fw={600}>История изменений профиля</Text></Group>
            {history.length === 0 ? <Text c="dimmed" size="sm">История пуста.</Text> : (
              <ScrollArea.Autosize mah={480}>
                <Table striped>
                  <Table.Thead><Table.Tr><Table.Th>Дата</Table.Th><Table.Th>Версия</Table.Th><Table.Th /></Table.Tr></Table.Thead>
                  <Table.Tbody>{history.map(h => (
                    <Table.Tr key={h.id}>
                      <Table.Td>{new Date(h.created_at).toLocaleString('ru-RU')}</Table.Td>
                      <Table.Td><Text size="sm" c="dimmed">{h.profile_data?.full_name} ({h.profile_data?.caste})</Text></Table.Td>
                      <Table.Td ta="right"><Button size="xs" variant="light" leftSection={<RotateCcw size={13} />} onClick={() => rollback(h.id)}>Откатить</Button></Table.Td>
                    </Table.Tr>))}</Table.Tbody>
                </Table>
              </ScrollArea.Autosize>
            )}
          </Stack>
        </Tabs.Panel>
      </Tabs>

      <EntityPicker
        opened={pickAccount} onClose={() => setPickAccount(false)} title="Привязать свободный аккаунт"
        rows={freeAccounts} rowKey={a => a.id}
        searchText={a => `${a.platform} ${a.username} ${a.status}`}
        emptyText="Нет свободных аккаунтов."
        columns={[
          { key: 'platform', header: 'Платформа', minWidth: 120, render: a => <Badge variant="outline">{a.platform}</Badge> },
          { key: 'username', header: 'Имя', minWidth: 200, render: a => <Text ff="monospace">{a.username}</Text> },
          { key: 'status', header: 'Статус', minWidth: 120, render: a => <Badge size="sm" color={STATUS_COLOR[a.status] || 'gray'} variant="light">{a.status}</Badge> },
        ]}
        onPick={bindAccount}
      />

      <EntityPicker
        opened={pickSim} onClose={() => setPickSim(false)}
        title="Личность из Симуляции (полигон)"
        rows={simPersonas} rowKey={(s: any) => s.id}
        searchText={(s: any) => `${s.codename} ${s.agent_key} ${s.caste} ${(s.interests || []).join(' ')}`}
        emptyText="В симуляции пока нет агентов."
        columns={[
          { key: 'codename', header: 'Позывной', minWidth: 160, render: (s: any) => <Text fw={600}>{s.codename}</Text> },
          { key: 'caste', header: 'Каста', minWidth: 100, render: (s: any) => <Badge size="sm" color={CASTE_COLOR[s.caste] || 'gray'} variant="light">{s.caste}</Badge> },
          { key: 'bio', header: 'Кто это', minWidth: 260, render: (s: any) => <Text size="sm" lineClamp={2}>{s.bio || '—'}</Text> },
          { key: 'interests', header: 'Интересы', minWidth: 200, sortable: false, render: (s: any) => <Group gap={4}>{(s.interests || []).slice(0, 4).map((i: string) => <Badge key={i} size="xs" variant="dot">{i}</Badge>)}</Group> },
        ]}
        onPick={(s: any) => applySimPersona(s.id)}
      />
    </DetailPage>
  );
}
