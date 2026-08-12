import { useState, useEffect, useCallback } from 'react';
import {
  Alert, Box, Group, Stack, Title, Text, Badge, Button, Paper, Tabs, TextInput, Textarea, Select,
  NumberInput, ActionIcon, SimpleGrid, Tooltip, TagsInput,
} from '@mantine/core';
import { Target, Plus, Play, Pause, Trash2, Check, X, Trash, Sparkles, UserPlus } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';
import { EntityPicker } from '../ui/EntityPicker';

interface SquadMember { id: number; agent_id: string; assigned_role: string; status: string; codename?: string | null; }
interface MTarget {
  id: number; kind: string; identifier: string; title: string | null; status: string;
  source: string; proposed_by: string | null; reason: string | null;
  // Stage 38 — can the swarm actually comment there (comments enabled, account allowed)?
  health: string; health_reason: string | null; health_checked_at: string | null;
}

const HEALTH_LABEL: Record<string, string> = {
  ok: 'работает', blocked: 'не работает', degraded: 'сбои', unknown: 'не проверено',
};
const HEALTH_COLOR: Record<string, string> = {
  ok: 'teal', blocked: 'red', degraded: 'orange', unknown: 'gray',
};

/** Target health chip — a mission that never posts is usually a blocked target. */
function TargetHealthBadge({ health }: { health: string }) {
  const h = health || 'unknown';
  return <Badge size="xs" variant={h === 'ok' ? 'light' : 'filled'} color={HEALTH_COLOR[h] || 'gray'}>
    {HEALTH_LABEL[h] || h}
  </Badge>;
}
interface Mission {
  id: number; title: string; platform: string; narrative_goal: string | null; stance: string | null;
  our_side: string | null; opponent: string | null; key_points: string[]; red_lines: string[];
  phase: string; recon_at: string | null;
  tactic: string; status: string; agent_mode: string; dynamic_count: number; forced_context: string | null;
  squad: SquadMember[]; targets: MTarget[];
  summary: {
    status_label: string; agents: Record<string, number>; targets: Record<string, number>;
    target_health?: Record<string, number>;
  };
}
interface EligibleAgent { agent_id: string; codename: string | null; caste: string; status: string; active_mission_load: number; at_capacity: boolean; already_enlisted: boolean; match_score: number; match_reasons: string[]; }

export interface DossierEntry {
  id: number; kind: string; content: string; source_url: string | null;
  added_by: string; post_url: string | null; times_used: number;
}
export interface Outcome {
  id: number; post_url: string; channel_ref: string;
  mood_before: string | null; mood_after: string | null;
  thread_size_before: number; thread_size_after: number; thread_grew: boolean;
  our_comments: number; human_replies: number; measured_at: string | null;
}
const PHASES = [
  { value: 'draft', label: 'Черновик', hint: 'формулируется' },
  { value: 'recon', label: 'Разведка', hint: 'собирает факты, не спорит' },
  { value: 'ready', label: 'Готова', hint: 'досье собрано, ждёт запуска' },
  { value: 'active', label: 'В работе', hint: 'действует' },
];
const PHASE_COLOR: Record<string, string> = {
  draft: 'gray', recon: 'blue', ready: 'yellow', active: 'teal', paused: 'orange',
};
const MOOD_RU: Record<string, string> = { AGREE: 'за нас', NEUTRAL: 'нейтрально', OPPOSE: 'против нас' };

export interface MissionPrefill { target_url: string; title: string; narrative_goal: string; }
interface Props {
  token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void;
  prefill?: MissionPrefill | null; onPrefillConsumed?: () => void; goTo?: (view: string, id?: string) => void;
}

const TACTICS = [
  { value: 'dynamic', label: 'Динамическая (по ситуации)' },
  { value: 'soft_support', label: 'Мягкая поддержка' },
  { value: 'aggressive_displacement', label: 'Жёсткое вытеснение' },
];
const ROLE_COLOR: Record<string, string> = { alpha: 'red', beta: 'blue', gamma: 'green' };
const inferKind = (id: string) => (/\/\d+\/?$/.test(id) ? 'post' : 'channel');

export default function MissionsScreen({ token, selectedId, onOpen, onBack, prefill, onPrefillConsumed, goTo }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchMissions = useCallback(async () => {
    setLoading(true);
    try { const r = await fetch('/api/v1/missions', { headers }); if (r.ok) { setMissions(await r.json()); setError(''); } }
    catch (e: any) { setError(e.message || 'Не удалось загрузить миссии'); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchMissions(); }, [fetchMissions]);

  // ScoutingRadar "convert" prefill → open the create form prefilled.
  useEffect(() => {
    if (prefill) { onOpen('new'); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  // Stage 45 — the list drives PHASES, not the old on/off status: a mission may not
  // go active before its dossier exists, and the phase endpoint enforces that.
  const setPhaseFromList = async (m: Mission, phase: string) => {
    const r = await fetch(`/api/v1/missions/${m.id}/phase`, { method: 'POST', headers, body: JSON.stringify({ phase }) });
    if (r.ok) { const u = await r.json(); setMissions(prev => prev.map(x => x.id === u.id ? u : x)); }
  };

  if (selectedId === 'new') {
    return <MissionCreate token={token} prefill={prefill || null} onCreated={() => { onPrefillConsumed?.(); fetchMissions(); }} onBack={() => { onPrefillConsumed?.(); onBack(); }} onOpen={onOpen} />;
  }
  if (selectedId) {
    const m = missions.find(x => String(x.id) === selectedId);
    if (!m) return <DetailPage onBack={onBack} title="Загрузка…" subtitle={selectedId}><Text c="dimmed">Миссия загружается…</Text></DetailPage>;
    return <MissionDetail key={m.id} token={token} mission={m} onBack={onBack}
      onChanged={(u) => setMissions(prev => prev.map(x => x.id === u.id ? u : x))}
      onDeleted={() => { fetchMissions(); onBack(); }} goTo={goTo} />;
  }

  const columns: Col<Mission>[] = [
    { key: 'title', header: 'Миссия', minWidth: 320, sortValue: m => m.title.toLowerCase(),
      render: m => (<Stack gap={2}><Text fw={600}>{m.title}</Text>{m.narrative_goal && <Text size="xs" c="dimmed" lineClamp={1}>🎯 {m.narrative_goal}</Text>}</Stack>) },
    { key: 'status', header: 'Статус', minWidth: 120, sortValue: m => m.status,
      render: m => (
        <Badge color={PHASE_COLOR[m.phase] || 'gray'} variant="light"
               style={{ cursor: 'pointer' }}
               onClick={e => { e.stopPropagation(); setPhaseFromList(m, m.phase === 'active' ? 'paused' : 'active'); }}>
          {PHASES.find(p => p.value === m.phase)?.label || m.phase}
        </Badge>) },
    { key: 'agents', header: 'Агенты', minWidth: 90, align: 'right', sortValue: m => m.summary.agents.total || 0,
      render: m => m.summary.agents.total || 0 },
    { key: 'targets', header: 'Цели', minWidth: 170, align: 'right', sortValue: m => m.summary.targets.active || 0,
      render: m => (<Group gap={6} justify="flex-end">{m.summary.targets.active || 0}
        {(m.summary.targets.suggested || 0) > 0 && <Badge size="xs" color="yellow" variant="light">⏳{m.summary.targets.suggested}</Badge>}
        {/* A mission whose targets are all unusable looks "active" but can never post. */}
        {(m.summary.target_health?.blocked || 0) > 0 && (
          <Tooltip label="Целей, где комментировать невозможно">
            <Badge size="xs" color="red">⚠{m.summary.target_health?.blocked}</Badge>
          </Tooltip>)}</Group>) },
    { key: 'tactic', header: 'Тактика', minWidth: 160, render: m => TACTICS.find(t => t.value === m.tactic)?.label || m.tactic },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Target size={22} style={{ verticalAlign: -4 }} /> Миссии</Title>
          <Text size="sm" c="dimmed">Постоянные цели роя — нажмите на строку, чтобы открыть карточку миссии (цели, агенты, параметры).</Text>
        </div>
        <Button leftSection={<Plus size={16} />} onClick={() => onOpen('new')}>Новая миссия</Button>
      </Group>
      {error && <Text c="red" mb="sm">{error}</Text>}
      <DataView
        columns={columns} rows={missions} rowKey={m => m.id} loading={loading}
        searchText={m => `${m.title} ${m.narrative_goal || ''} ${m.stance || ''}`}
        searchPlaceholder="🔍 Поиск по названию, цели, позиции…"
        emptyText="Миссий пока нет — создайте новую."
        onRowClick={m => onOpen(String(m.id))}
      />
    </Box>
  );
}

// ── Create ──
function MissionCreate({ token, prefill, onCreated, onBack, onOpen }: {
  token: string; prefill: MissionPrefill | null; onCreated: () => void; onBack: () => void; onOpen: (id: string) => void;
}) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [title, setTitle] = useState(prefill?.title || '');
  const [goal, setGoal] = useState(prefill?.narrative_goal || '');
  const [stance, setStance] = useState('');
  const [tactic, setTactic] = useState('dynamic');
  const [mode, setMode] = useState('manual');
  const [count, setCount] = useState(3);
  const [targets, setTargets] = useState(prefill?.target_url || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const create = async () => {
    setSaving(true); setError('');
    const tlist = targets.split(/[\n,]+/).map(s => s.trim()).filter(Boolean).map(id => ({ identifier: id, kind: inferKind(id) }));
    try {
      const r = await fetch('/api/v1/missions', { method: 'POST', headers, body: JSON.stringify({ title, narrative_goal: goal, stance, tactic, agent_mode: mode, dynamic_count: count, targets: tlist }) });
      if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `HTTP ${r.status}`); }
      const m = await r.json();
      onCreated();
      onOpen(String(m.id));
    } catch (e: any) { setError(e.message || 'Не удалось создать миссию'); }
    finally { setSaving(false); }
  };

  return (
    <DetailPage onBack={onBack} title="Новая миссия"
      footer={<>{error && <Text c="red" size="sm" mr="auto">{error}</Text>}<Button variant="default" onClick={onBack}>Отмена</Button><Button loading={saving} disabled={!title.trim()} onClick={create}>Создать</Button></>}>
      <Stack gap="md" maw={720}>
        <TextInput label="Название" value={title} onChange={e => setTitle(e.currentTarget.value)} placeholder="напр. Поддержка общественного транспорта" />
        <Textarea label="Цель (что продвигать)" autosize minRows={2} value={goal} onChange={e => setGoal(e.currentTarget.value)} placeholder="Продвигать развитие и финансирование общественного транспорта." />
        <Textarea label="«Правда» / сторона / видение" autosize minRows={3} value={stance} onChange={e => setStance(e.currentTarget.value)} placeholder="Мировоззрение миссии: за что стоим — этим агенты руководствуются в спорах." />
        <Group grow>
          <Select label="Тактика по умолчанию" data={TACTICS} value={tactic} onChange={v => v && setTactic(v)} />
          <Select label="Набор агентов" data={[{ value: 'manual', label: 'вручную' }, { value: 'dynamic', label: 'динамически' }]} value={mode} onChange={v => v && setMode(v)} />
          {mode === 'dynamic' && <NumberInput label="Сколько" min={0} max={50} value={count} onChange={v => setCount(Number(v) || 0)} />}
        </Group>
        <Textarea label="Цели (каналы/посты, по одному в строке)" autosize minRows={3} value={targets} onChange={e => setTargets(e.currentTarget.value)} placeholder={'@tashkent_news333\nhttps://t.me/somechannel/123'} />
      </Stack>
    </DetailPage>
  );
}

// ── Detail ──
function MissionDetail({ token, mission, onBack, onChanged, onDeleted, goTo }: {
  token: string; mission: Mission; onBack: () => void; onChanged: (m: Mission) => void;
  onDeleted: () => void; goTo?: (view: string, id?: string) => void;
}) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [m, setM] = useState<Mission>(mission);
  const [title, setTitle] = useState(m.title);
  const [goal, setGoal] = useState(m.narrative_goal || '');
  const [stance, setStance] = useState(m.stance || '');
  const [ourSide, setOurSide] = useState(m.our_side || '');
  const [opponent, setOpponent] = useState(m.opponent || '');
  const [keyPoints, setKeyPoints] = useState<string[]>(m.key_points || []);
  const [redLines, setRedLines] = useState<string[]>(m.red_lines || []);
  const [tactic, setTactic] = useState(m.tactic);
  const [newTarget, setNewTarget] = useState('');
  const [eligible, setEligible] = useState<EligibleAgent[]>([]);
  const [pickAgent, setPickAgent] = useState(false);
  const [saving, setSaving] = useState(false);
  // Sanity checks. A mission that argues with itself is not a typo — mission #14
  // («За аргентину», stance «проиграл из-за тренера») published a comment agreeing
  // with the defeat it existed to dispute. Advisory only: it never blocks saving.
  const [issues, setIssues] = useState<{ field: string; severity: string; message: string }[]>([]);
  const [checking, setChecking] = useState(false);
  // Stage 45 — the mission has a lifecycle, not a switch. It must pass through
  // investigation before it may argue, so the header drives phases and the engine
  // refuses `active` while the dossier is empty.
  const [dossier, setDossier] = useState<DossierEntry[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [reconBusy, setReconBusy] = useState(false);
  const [phaseError, setPhaseError] = useState('');
  const [reconReport, setReconReport] = useState<any>(null);

  const loadDossier = useCallback(async () => {
    try {
      const r = await fetch(`/api/v1/missions/${m.id}/dossier`, { headers });
      if (r.ok) setDossier((await r.json()).entries || []);
    } catch { /* advisory */ }
    try {
      const r = await fetch(`/api/v1/missions/${m.id}/outcomes`, { headers });
      if (r.ok) setOutcomes((await r.json()).outcomes || []);
    } catch { /* advisory */ }
  }, [m.id, token]);
  useEffect(() => { loadDossier(); }, [loadDossier]);

  const setPhase = async (phase: string) => {
    setPhaseError('');
    const r = await fetch(`/api/v1/missions/${m.id}/phase`, {
      method: 'POST', headers, body: JSON.stringify({ phase }) });
    if (r.ok) apply(await r.json());
    else setPhaseError((await r.json()).detail || 'не удалось сменить фазу');
  };

  const runRecon = async () => {
    setReconBusy(true); setPhaseError(''); setReconReport(null);
    try {
      const r = await fetch(`/api/v1/missions/${m.id}/recon`, { method: 'POST', headers });
      const rep = await r.json();
      setReconReport(rep);
      loadDossier();
      const mr = await fetch(`/api/v1/missions/${m.id}`, { headers });
      if (mr.ok) apply(await mr.json());
    } catch (e: any) { setPhaseError(String(e)); } finally { setReconBusy(false); }
  };

  const apply = (u: Mission) => { setM(u); onChanged(u); };

  const validate = useCallback(async () => {
    setChecking(true);
    try {
      const r = await fetch(`/api/v1/missions/${m.id}/validate`, { headers });
      if (r.ok) setIssues((await r.json()).issues || []);
    } catch { /* advisory — never block the editor */ } finally { setChecking(false); }
  }, [m.id, token]);
  useEffect(() => { validate(); }, [validate]);

  const save = async () => {
    setSaving(true);
    const r = await fetch(`/api/v1/missions/${m.id}`, { method: 'PUT', headers, body: JSON.stringify({ title, narrative_goal: goal, stance, tactic,
        our_side: ourSide, opponent, key_points: keyPoints, red_lines: redLines }) });
    if (r.ok) apply(await r.json());
    setSaving(false);
    validate();
  };
  const addTarget = async () => {
    const id = newTarget.trim(); if (!id) return;
    const r = await fetch(`/api/v1/missions/${m.id}/targets`, { method: 'POST', headers, body: JSON.stringify({ identifier: id, kind: inferKind(id) }) });
    if (r.ok) { apply(await r.json()); setNewTarget(''); }
  };
  const decideTarget = async (tid: number, decision: string) => {
    const r = await fetch(`/api/v1/missions/${m.id}/targets/${tid}/${decision}`, { method: 'POST', headers });
    if (r.ok) apply(await r.json());
  };
  const deleteTarget = async (tid: number) => {
    const r = await fetch(`/api/v1/missions/${m.id}/targets/${tid}`, { method: 'DELETE', headers });
    if (r.ok) apply(await r.json());
  };
  const removeAgent = async (sid: number) => {
    const r = await fetch(`/api/v1/missions/${m.id}/squad/${sid}`, { method: 'DELETE', headers });
    if (r.ok) apply(await r.json());
  };
  const fetchEligible = useCallback(async () => {
    const r = await fetch(`/api/v1/missions/${m.id}/eligible-agents`, { headers });
    if (r.ok) setEligible(await r.json());
  }, [m.id, token]);
  useEffect(() => { fetchEligible(); }, [fetchEligible]);
  const enlist = async (agent_id: string, role: string) => {
    const r = await fetch(`/api/v1/missions/${m.id}/squad`, { method: 'POST', headers, body: JSON.stringify({ agent_id, assigned_role: role }) });
    if (r.ok) { apply(await r.json()); fetchEligible(); }
  };
  const autoAssign = async () => {
    const r = await fetch(`/api/v1/missions/${m.id}/auto-assign`, { method: 'POST', headers, body: JSON.stringify({ alpha: 1, beta: 2, gamma: 1 }) });
    if (r.ok) apply(await r.json());
  };
  const remove = async () => {
    if (!confirm(`Удалить миссию «${m.title}»?`)) return;
    const r = await fetch(`/api/v1/missions/${m.id}`, { method: 'DELETE', headers });
    if (r.ok) onDeleted();
  };

  const freeEligible = eligible.filter(e => !e.already_enlisted && !e.at_capacity);

  return (
    <DetailPage
      onBack={onBack}
      title={m.title}
      subtitle={<Text span size="sm" c="dimmed">постоянная цель · {m.summary.agents.total || 0} агент(ов) · {m.summary.targets.active || 0} цел.</Text>}
      headerRight={
        <Group gap="xs">
          <Badge size="lg" color={PHASE_COLOR[m.phase] || 'gray'} variant="light">
            {PHASES.find(p => p.value === m.phase)?.label || m.phase}
          </Badge>
          {m.phase !== 'active'
            ? <Button variant="light" color="teal" leftSection={<Play size={15} />}
                onClick={() => setPhase('active')}>В работу</Button>
            : <Button variant="light" color="orange" leftSection={<Pause size={15} />}
                onClick={() => setPhase('paused')}>Пауза</Button>}
        </Group>
      }
    >
      <Tabs defaultValue="overview" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="overview">Обзор</Tabs.Tab>
          <Tabs.Tab value="dossier">Досье ({dossier.length})</Tabs.Tab>
          <Tabs.Tab value="results">Результат ({outcomes.length})</Tabs.Tab>
          <Tabs.Tab value="targets">Цели ({m.summary.targets.active || 0}){(m.summary.targets.suggested || 0) > 0 ? ` · ⏳${m.summary.targets.suggested}` : ''}</Tabs.Tab>
          <Tabs.Tab value="agents">Агенты ({m.summary.agents.total || 0})</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="overview">
          <Stack gap="md" maw={720}>
            {/* The lifecycle, in the order it is meant to happen. A mission may not
                argue about something it has not established. */}
            <Paper withBorder p="sm" radius="sm">
              <Group justify="space-between" mb={6}>
                <Text fw={600} size="sm">Этап миссии</Text>
                <Button size="compact-xs" variant="light" loading={reconBusy} onClick={runRecon}>
                  Запустить разведку
                </Button>
              </Group>
              <Group gap={6} wrap="wrap">
                {PHASES.map(p => (
                  <Badge key={p.value} variant={m.phase === p.value ? 'filled' : 'light'}
                         color={m.phase === p.value ? PHASE_COLOR[p.value] : 'gray'}
                         style={{ cursor: 'pointer' }} onClick={() => setPhase(p.value)}>
                    {p.label}
                  </Badge>
                ))}
              </Group>
              <Text size="xs" c="dimmed" mt={6}>
                {PHASES.find(p => p.value === m.phase)?.hint || 'на паузе'} · фактов в досье:{' '}
                {dossier.filter(d => d.kind === 'fact').length}
              </Text>
              {phaseError && <Alert color="red" mt="sm">{phaseError}</Alert>}
              {reconReport && (
                <Alert color={reconReport.filed ? 'teal' : 'yellow'} mt="sm">
                  Просмотрено {reconReport.scanned}, взято в досье {reconReport.filed}.
                  {reconReport.reason ? ` ${reconReport.reason}` : ''}
                </Alert>
              )}
            </Paper>
            {issues.length > 0 && (
              <Alert color={issues.some(i => i.severity === 'error') ? 'red' : 'yellow'}
                     title="Проверка миссии">
                <Stack gap={4}>
                  {issues.map((i, n) => (
                    <Text key={n} size="sm">
                      <b>{i.field}</b> — {i.message}
                    </Text>
                  ))}
                  <Text size="xs" c="dimmed">
                    Это подсказка, а не запрет — сохранить можно в любом случае.
                  </Text>
                </Stack>
              </Alert>
            )}
            {checking && <Text size="xs" c="dimmed">Проверяю формулировки…</Text>}
            <TextInput label="Название" value={title} onChange={e => setTitle(e.currentTarget.value)} />
            <Textarea label="Цель" autosize minRows={2} value={goal} onChange={e => setGoal(e.currentTarget.value)} />
            <Textarea label="«Правда» / сторона" autosize minRows={3} value={stance} onChange={e => setStance(e.currentTarget.value)} />
            {/* Явная позиция: без неё модель угадывает, за кого она, и путает стороны. */}
            <Group grow align="flex-start">
              <TextInput label="Мы за" value={ourSide} onChange={e => setOurSide(e.currentTarget.value)}
                placeholder="напр. сборная Аргентины" description="кого/что защищаем" />
              <TextInput label="Оппонент" value={opponent} onChange={e => setOpponent(e.currentTarget.value)}
                placeholder="напр. критики Месси" description="с чьей позицией спорим" />
            </Group>
            <TagsInput label="Доводы (тезисы)" value={keyPoints} onChange={setKeyPoints}
              description="3–5 коротких аргументов; агент возьмёт уместный" placeholder="Enter, чтобы добавить" />
            <TagsInput label="Красные линии" value={redLines} onChange={setRedLines}
              description="что агент НИКОГДА не должен говорить" placeholder="Enter, чтобы добавить" />
            <Select label="Тактика по умолчанию" data={TACTICS} value={tactic} onChange={v => v && setTactic(v)} w={320} />
            <Group>
              <Button loading={saving} onClick={save}>Сохранить</Button>
              <Button variant="subtle" color="red" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить миссию</Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="dossier">
          <Stack gap="sm" maw={860}>
            <Text size="sm" c="dimmed">
              Общая память команды: что установлено, что говорит другая сторона и какие
              доводы наши уже использовали. Ростер читает её целиком, поэтому агенты не
              повторяют друг друга.
            </Text>
            {dossier.length === 0 && (
              <Alert color="yellow">
                Досье пустое — миссия не выйдет в работу. Запустите разведку на вкладке «Обзор».
              </Alert>
            )}
            {['fact', 'opponent', 'counter', 'said'].map(kind => {
              const rows = dossier.filter(d => d.kind === kind);
              if (!rows.length) return null;
              const title = { fact: 'Установленные факты', opponent: 'Доводы другой стороны',
                              counter: 'Наши контрдоводы', said: 'Уже сказано нашими' }[kind];
              return (
                <Paper key={kind} withBorder p="sm" radius="sm">
                  <Text fw={600} size="sm" mb={6}>{title} ({rows.length})</Text>
                  <Stack gap={4}>
                    {rows.slice(0, 25).map(d => (
                      <Group key={d.id} justify="space-between" wrap="nowrap" align="flex-start">
                        <Text size="sm" style={{ minWidth: 0 }}>{d.content}</Text>
                        <Group gap={4} wrap="nowrap">
                          {d.times_used > 1 && <Badge size="xs" variant="light">×{d.times_used}</Badge>}
                          <Badge size="xs" variant="light" color="gray">{d.added_by}</Badge>
                        </Group>
                      </Group>
                    ))}
                  </Stack>
                </Paper>
              );
            })}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="results">
          <Stack gap="sm" maw={860}>
            <Text size="sm" c="dimmed">
              Успех — это сдвиг тона обсуждения и живые люди, втянувшиеся в разговор.
              Тон замеряется по репликам ПОСЛЕ нашего захода: одна реплика среди двадцати
              не двигает среднее по всей ветке, поэтому среднее ничего и не показывало.
            </Text>
            {outcomes.length === 0 && <Text c="dimmed" size="sm">Пока нет ни одного замера.</Text>}
            {outcomes.map(o => {
              const moved = o.mood_after && o.mood_before && o.mood_after !== o.mood_before;
              return (
                <Paper key={o.id} withBorder p="sm" radius="sm">
                  <Group justify="space-between" wrap="nowrap">
                    <Text size="sm" style={{ minWidth: 0 }} lineClamp={1}>{o.post_url}</Text>
                    {!o.measured_at
                      ? <Badge size="sm" variant="light" color="gray">ждёт замера</Badge>
                      : <Badge size="sm" variant="light" color={moved ? 'teal' : 'gray'}>
                          {moved ? 'тон сдвинулся' : 'без сдвига'}
                        </Badge>}
                  </Group>
                  <Text size="xs" c="dimmed" mt={4}>
                    тон: {MOOD_RU[o.mood_before || ''] || '—'} →{' '}
                    {o.measured_at ? (MOOD_RU[o.mood_after || ''] || 'после нас никто не писал') : '—'}
                    {' · '}ветка: {o.thread_size_before} → {o.thread_size_after}
                    {' · '}наших реплик: {o.our_comments}
                    {' · '}ответили живые: {o.human_replies}
                  </Text>
                </Paper>
              );
            })}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="targets">
          <Stack gap="md" maw={820}>
            <Group align="flex-end">
              <TextInput style={{ flex: 1 }} label="Добавить цель (@канал или ссылка на пост)" value={newTarget}
                onChange={e => setNewTarget(e.currentTarget.value)} placeholder="@channel или https://t.me/.../123" />
              <Button leftSection={<Plus size={15} />} onClick={addTarget}>Добавить</Button>
            </Group>
            {m.targets.length === 0 ? <Text c="dimmed" size="sm">Целей нет.</Text> : (
              <Stack gap="xs">{m.targets.map(t => (
                <Paper key={t.id} withBorder p="sm" radius="md">
                  <Group justify="space-between" wrap="nowrap">
                    <Box style={{ minWidth: 0 }}>
                      <Group gap={6}>
                        <Text fw={600} size="sm">{t.kind === 'post' ? '📄' : '📢'} {t.title || t.identifier}</Text>
                        {t.status === 'active' && <TargetHealthBadge health={t.health} />}
                      </Group>
                      <Text size="xs" c="dimmed">{t.identifier} · {t.source === 'agent' ? `предложил ${t.proposed_by || 'агент'}` : 'оператор'}{t.status === 'rejected' ? ' · отклонено' : ''}</Text>
                      {t.reason && <Text size="xs" c="dimmed">{t.reason}</Text>}
                      {t.health === 'blocked' && t.health_reason && (
                        <Text size="xs" c="red">⚠ {t.health_reason}{t.health_checked_at ? ` · проверено ${new Date(t.health_checked_at).toLocaleString('ru-RU')}` : ''}</Text>
                      )}
                      {t.health === 'degraded' && t.health_reason && (
                        <Text size="xs" c="orange">{t.health_reason}</Text>
                      )}
                    </Box>
                    <Group gap={6} wrap="nowrap">
                      {t.status === 'suggested' && <>
                        <Button size="xs" variant="light" color="teal" leftSection={<Check size={13} />} onClick={() => decideTarget(t.id, 'approve')}>Принять</Button>
                        <Button size="xs" variant="light" color="orange" leftSection={<X size={13} />} onClick={() => decideTarget(t.id, 'reject')}>Отклонить</Button>
                      </>}
                      <ActionIcon variant="subtle" color="red" onClick={() => deleteTarget(t.id)}><Trash size={15} /></ActionIcon>
                    </Group>
                  </Group>
                </Paper>))}</Stack>
            )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="agents">
          <Stack gap="md" maw={760}>
            <Group justify="space-between">
              <Text fw={600}>Ростер миссии</Text>
              <Group gap="xs">
                <Button size="xs" variant="light" leftSection={<UserPlus size={14} />} onClick={() => setPickAgent(true)} disabled={freeEligible.length === 0}>Добавить агента</Button>
                <Button size="xs" variant="default" leftSection={<Sparkles size={14} />} onClick={autoAssign}>Авто-набор (1α/2β/1γ)</Button>
              </Group>
            </Group>
            {m.squad.length === 0 ? <Text c="dimmed" size="sm">Агенты не назначены.</Text> : (
              <Stack gap="xs">{m.squad.map(s => (
                <Paper key={s.id} withBorder p="sm" radius="md">
                  <Group justify="space-between">
                    <Group gap="sm"><Badge color={ROLE_COLOR[s.assigned_role] || 'gray'} variant="light">{s.assigned_role}</Badge><Text ff="monospace" size="sm">{s.codename || s.agent_id}</Text></Group>
                    <Group gap="xs">
                      {goTo && <Button size="xs" variant="subtle" onClick={() => goTo('souls', s.agent_id)}>душа →</Button>}
                      <ActionIcon variant="subtle" color="red" onClick={() => removeAgent(s.id)}><X size={15} /></ActionIcon>
                    </Group>
                  </Group>
                </Paper>))}</Stack>
            )}
            <SimpleGrid cols={3} mt="xs">
              {(['alpha', 'beta', 'gamma'] as const).map(c => (
                <Paper key={c} withBorder p="xs" radius="md" ta="center">
                  <Text size="xs" c="dimmed" tt="uppercase">{c}</Text>
                  <Text fw={700} c={ROLE_COLOR[c]}>{m.squad.filter(s => s.assigned_role === c).length}</Text>
                </Paper>))}
            </SimpleGrid>
          </Stack>
        </Tabs.Panel>
      </Tabs>

      <EntityPicker
        opened={pickAgent} onClose={() => setPickAgent(false)} title="Добавить агента в миссию"
        rows={freeEligible} rowKey={e => e.agent_id}
        searchText={e => `${e.codename || ''} ${e.agent_id} ${e.caste}`}
        emptyText="Нет доступных агентов."
        columns={[
          { key: 'agent', header: 'Агент', minWidth: 220, render: e => <Stack gap={0}><Text fw={600}>{e.codename || e.agent_id}</Text><Text size="xs" c="dimmed" ff="monospace">{e.agent_id}</Text></Stack> },
          { key: 'caste', header: 'Каста', minWidth: 90, render: e => <Badge color={ROLE_COLOR[e.caste] || 'gray'} variant="light">{e.caste}</Badge> },
          { key: 'match', header: 'Совпадение', minWidth: 120, align: 'right', sortValue: e => e.match_score, render: e => `${Math.round(e.match_score * 100)}%` },
          { key: 'load', header: 'Загрузка', minWidth: 90, align: 'right', sortValue: e => e.active_mission_load, render: e => e.active_mission_load },
        ]}
        onPick={e => enlist(e.agent_id, e.caste)}
      />
    </DetailPage>
  );
}
