/**
 * Simulation agents (AI personas under test) and simulation-only missions.
 *
 * A persona is fully self-contained — identity, interests, style sliders and the
 * system prompt travel with the generation request, so tuning one here can never
 * change a production soul. The export action shapes a tested persona as a
 * production soul draft for the operator to reuse in the real Souls screen.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Code, Divider, Group, Modal, MultiSelect, NumberInput, Paper,
  ScrollArea, Select, SegmentedControl, Slider, Stack, TagsInput, Text, TextInput, Textarea, Tabs,
} from '@mantine/core';
import { Play, Save, Trash2, Download, Copy } from 'lucide-react';
import { SimApi, SimChannel, SimMission, SimPersona, SimPost, TACTICS } from './api';

const COLORS = ['violet', 'indigo', 'blue', 'teal', 'green', 'orange', 'red', 'pink', 'grape', 'cyan', 'gray'];
const CASTES = [
  { value: 'alpha', label: 'alpha — ведущий (полный когнитивный цикл)' },
  { value: 'beta', label: 'beta — поддержка (дешёвая генерация)' },
  { value: 'gamma', label: 'gamma — фон / реакции' },
];

function StyleSlider({ label, value, onChange, left, right }: {
  label: string; value: number; onChange: (v: number) => void; left: string; right: string;
}) {
  return (
    <Stack gap={2}>
      <Group justify="space-between">
        <Text size="sm">{label}</Text><Badge size="sm" variant="light">{value}</Badge>
      </Group>
      <Slider min={1} max={10} step={1} value={value} onChange={onChange}
        marks={[{ value: 1, label: left }, { value: 10, label: right }]} mb="md" />
    </Stack>
  );
}

export function PersonaModal({ opened, onClose, api, worldId, persona, onSaved }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number;
  persona: SimPersona | null; onSaved: () => void;
}) {
  const empty = {
    agent_key: '', codename: '', full_name: '', caste: 'alpha', status: 'active', color: 'violet',
    bio: '', core_mission: '', interests: [] as string[], system_prompt: '',
    style: { tone_level: 5, vocab_level: 5, emoji_frequency: 3, aggression: 3, quirks: [] as string[], language: 'ru' },
  };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState('');
  const [exported, setExported] = useState<Record<string, any> | null>(null);
  const [autosaved, setAutosaved] = useState('');
  const dirty = useRef(false);

  useEffect(() => {
    if (!opened) return;
    setExported(null); setError(''); setAutosaved('');
    dirty.current = false;
    setForm(persona ? {
      agent_key: persona.agent_key, codename: persona.codename, full_name: persona.full_name || '',
      caste: persona.caste, status: persona.status, color: persona.color, bio: persona.bio || '',
      core_mission: persona.core_mission || '', interests: persona.interests || [],
      system_prompt: persona.system_prompt || '',
      style: { ...empty.style, ...(persona.style || {}) },
    } : { ...empty, agent_key: `sim_${Math.random().toString(36).slice(2, 8)}` });
  }, [opened, persona]);

  const setStyle = (key: string, value: any) => {
    dirty.current = true;
    setForm(f => ({ ...f, style: { ...f.style, [key]: value } }));
  };

  // Автосохранение: настроенные паттерны существующего агента сохраняются сами,
  // чтобы отладка личности не терялась при закрытии окна.
  useEffect(() => {
    if (!opened || !persona) return;
    if (!dirty.current) return;
    const t = setTimeout(async () => {
      try {
        await api.updatePersona(persona.id, { ...form, world_id: worldId, settings: persona.settings || {} });
        setAutosaved(new Date().toLocaleTimeString('ru-RU'));
        onSaved();
      } catch { /* the explicit Save button surfaces real failures */ }
    }, 900);
    return () => clearTimeout(t);
  }, [form, opened, persona, api, worldId]);

  const patch = (values: Partial<typeof empty>) => { dirty.current = true; setForm(f => ({ ...f, ...values })); };

  const save = async () => {
    setError('');
    try {
      const body = { ...form, world_id: worldId, settings: persona?.settings || {} };
      if (persona) await api.updatePersona(persona.id, body); else await api.createPersona(body);
      onSaved(); onClose();
    } catch (e: any) { setError(e.message); }
  };

  const remove = async () => {
    if (!persona || !confirm(`Удалить агента «${persona.codename}»?`)) return;
    try { await api.deletePersona(persona.id); onSaved(); onClose(); } catch (e: any) { setError(e.message); }
  };

  const doExport = async () => {
    if (!persona) return;
    try { setExported(await api.exportPersona(persona.id)); } catch (e: any) { setError(e.message); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="lg" centered
      title={persona ? `Агент «${persona.codename}»` : 'Новый агент (ИИ-личность)'}>
      <Tabs defaultValue="identity">
        <Tabs.List mb="sm">
          <Tabs.Tab value="identity">Личность</Tabs.Tab>
          <Tabs.Tab value="style">Стиль</Tabs.Tab>
          <Tabs.Tab value="prompt">Системный промпт</Tabs.Tab>
          {persona && <Tabs.Tab value="export">Экспорт</Tabs.Tab>}
        </Tabs.List>
        {error && <Alert color="red" mb="sm">{error}</Alert>}

        <Tabs.Panel value="identity">
          <Stack gap="sm">
            <Group grow>
              <TextInput label="Ключ агента" required value={form.agent_key}
                onChange={e => patch({ agent_key: e.currentTarget.value })} />
              <TextInput label="Позывной" required value={form.codename}
                onChange={e => patch({ codename: e.currentTarget.value })} />
            </Group>
            <Group grow>
              <TextInput label="Полное имя" value={form.full_name}
                onChange={e => patch({ full_name: e.currentTarget.value })} />
              <Select label="Каста" data={CASTES} value={form.caste}
                onChange={v => patch({ caste: v || 'alpha' })} />
            </Group>
            <Group grow>
              <Select label="Статус" data={[{ value: 'active', label: 'активен' }, { value: 'paused', label: 'на паузе' }]}
                value={form.status} onChange={v => patch({ status: v || 'active' })} />
              <Select label="Цвет" data={COLORS} value={form.color}
                onChange={v => patch({ color: v || 'violet' })} />
            </Group>
            <Textarea label="Кто это (био)" autosize minRows={2} value={form.bio}
              onChange={e => patch({ bio: e.currentTarget.value })}
              placeholder="инженер-транспортник из Ташкента, 34 года" />
            <Textarea label="Постоянная цель" autosize minRows={2} value={form.core_mission}
              onChange={e => patch({ core_mission: e.currentTarget.value })} />
            <TagsInput label="Интересы" value={form.interests}
              onChange={v => patch({ interests: v })} placeholder="Enter, чтобы добавить" />
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="style">
          <Stack gap="xs">
            <StyleSlider label="Тон" value={form.style.tone_level} left="официально" right="разговорно"
              onChange={v => setStyle('tone_level', v)} />
            <StyleSlider label="Словарь" value={form.style.vocab_level} left="простой" right="эрудит"
              onChange={v => setStyle('vocab_level', v)} />
            <StyleSlider label="Эмодзи" value={form.style.emoji_frequency} left="никогда" right="часто"
              onChange={v => setStyle('emoji_frequency', v)} />
            <StyleSlider label="Агрессивность" value={form.style.aggression} left="мягко" right="жёстко"
              onChange={v => setStyle('aggression', v)} />
            <TagsInput label="Речевые особенности" value={form.style.quirks || []}
              onChange={v => setStyle('quirks', v)} placeholder="напр. пишет без точек" />
            <TextInput label="Язык" value={form.style.language || 'ru'}
              onChange={e => setStyle('language', e.currentTarget.value)} />
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="prompt">
          <Stack gap="sm">
            <Text size="xs" c="dimmed">
              Этот текст вставляется в промпт как «Системная установка» перед персоной. Оставьте пустым,
              чтобы использовать стандартную сборку промпта движка.
            </Text>
            <Textarea autosize minRows={10} value={form.system_prompt}
              onChange={e => patch({ system_prompt: e.currentTarget.value })}
              placeholder="Ты — сварливый сосед, который во всём видит подвох…" />
          </Stack>
        </Tabs.Panel>

        {persona && (
          <Tabs.Panel value="export">
            <Stack gap="sm">
              <Text size="sm" c="dimmed">
                Отдаёт отлаженную личность в формате боевой души — можно использовать при создании
                или редактировании настоящего агента в разделе «Души». Ничего в боевой базе не меняется.
              </Text>
              <Button variant="light" leftSection={<Download size={15} />} onClick={doExport}>
                Подготовить для основной системы
              </Button>
              {exported && (
                <>
                  <ScrollArea h={220}>
                    <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 11 }}>
                      {JSON.stringify(exported, null, 2)}
                    </Code>
                  </ScrollArea>
                  <Button variant="default" leftSection={<Copy size={15} />}
                    onClick={() => navigator.clipboard?.writeText(JSON.stringify(exported, null, 2))}>
                    Скопировать JSON
                  </Button>
                </>
              )}
            </Stack>
          </Tabs.Panel>
        )}
      </Tabs>

      <Group justify="space-between" mt="md">
        {persona
          ? <Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить</Button>
          : <span />}
        <Group gap="sm">
          {autosaved && <Text size="xs" c="dimmed">автосохранено в {autosaved}</Text>}
          <Button variant="default" onClick={onClose}>Закрыть</Button>
          <Button onClick={save} leftSection={<Save size={15} />}>Сохранить</Button>
        </Group>
      </Group>
    </Modal>
  );
}

// ── Missions (simulation-only) ─────────────────────────────────────────────

export function MissionModal({ opened, onClose, api, worldId, mission, personas, channels, posts, onSaved, onJobStarted }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number; mission: SimMission | null;
  personas: SimPersona[]; channels: SimChannel[]; posts: SimPost[];
  onSaved: () => void; onJobStarted: (msg: string) => void;
}) {
  const [form, setForm] = useState({
    title: '', goal: '', stance: '', worldview: '', our_side: '', opponent: '',
    key_points: [] as string[], red_lines: [] as string[], tactic: 'dynamic', mode: 'comment',
    status: 'active', agent_ids: [] as string[], channel_ids: [] as string[],
  });
  const [run, setRun] = useState({ post_id: '', per_agent: 1, mode: 'generate_publish', pace_sec: 0 });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!opened) return;
    setError('');
    setForm(mission ? {
      title: mission.title, goal: mission.goal || '', stance: mission.stance || '',
      worldview: mission.worldview || '',
      our_side: mission.our_side || '', opponent: mission.opponent || '',
      key_points: mission.key_points || [], red_lines: mission.red_lines || [],
      tactic: mission.tactic, mode: mission.mode,
      status: mission.status, agent_ids: mission.agents.map(a => String(a.persona_id)),
      channel_ids: (mission.scope?.channel_ids || []).map(String),
    } : {
      title: '', goal: '', stance: '', worldview: '', our_side: '', opponent: '',
      key_points: [], red_lines: [], tactic: 'dynamic', mode: 'comment',
      status: 'active', agent_ids: [], channel_ids: [],
    });
    setRun(r => ({ ...r, post_id: mission?.scope?.post_id ? String(mission.scope.post_id) : '' }));
  }, [opened, mission]);

  const save = async () => {
    setError('');
    try {
      const body = {
        world_id: worldId, title: form.title, goal: form.goal, stance: form.stance,
        worldview: form.worldview, our_side: form.our_side, opponent: form.opponent,
        key_points: form.key_points, red_lines: form.red_lines,
        tactic: form.tactic, mode: form.mode, status: form.status,
        scope: {
          channel_ids: form.channel_ids.map(Number),
          ...(run.post_id ? { post_id: Number(run.post_id) } : {}),
        },
        settings: mission?.settings || {},
        agent_ids: form.agent_ids.map(Number),
      };
      if (mission) await api.updateMission(mission.id, body); else await api.createMission(body);
      onSaved(); onClose();
    } catch (e: any) { setError(e.message); }
  };

  const remove = async () => {
    if (!mission || !confirm(`Удалить миссию «${mission.title}»?`)) return;
    try { await api.deleteMission(mission.id); onSaved(); onClose(); } catch (e: any) { setError(e.message); }
  };

  const launch = async () => {
    if (!mission) return;
    setBusy(true); setError('');
    try {
      const job = await api.runMission(mission.id, {
        post_id: run.post_id ? Number(run.post_id) : null,
        per_agent: run.per_agent, mode: run.mode, pace_sec: run.pace_sec,
      });
      onJobStarted(`Миссия «${mission.title}» запущена: ${job.total} реплик в очереди.`);
      onSaved(); onClose();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="lg" centered
      title={mission ? `Миссия «${mission.title}»` : 'Новая миссия (только в симуляции)'}>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Alert color="teal" p="xs">
          <Text size="xs">
            Миссия симуляции живёт только на полигоне: у неё нет целей в реальном Telegram и её не видит
            боевой движок исполнения.
          </Text>
        </Alert>
        <TextInput label="Название" required value={form.title}
          onChange={e => setForm({ ...form, title: e.currentTarget.value })} />
        <Textarea label="Цель" autosize minRows={2} value={form.goal}
          onChange={e => setForm({ ...form, goal: e.currentTarget.value })}
          placeholder="что миссия продвигает" />
        <Textarea label="Мировоззрение / позиция" autosize minRows={2} value={form.stance}
          onChange={e => setForm({ ...form, stance: e.currentTarget.value })}
          placeholder="сторона, с которой спорят агенты — пишите утверждением, а не списком тегов" />
        {/* The explicit position — same four fields the live engine reads. Without
            them the model guesses whose side it is on, which is how a mission ends up
            arguing against its own goal. */}
        <Divider label="Сторона в споре — как в боевой миссии" labelPosition="left" />
        <Textarea label="Ты за" autosize minRows={2} value={form.our_side}
          onChange={e => setForm({ ...form, our_side: e.currentTarget.value })}
          placeholder="утверждение, которое агент защищает" />
        <Textarea label="Противоположная сторона" autosize minRows={1} value={form.opponent}
          onChange={e => setForm({ ...form, opponent: e.currentTarget.value })}
          placeholder="с чем агент НЕ согласен" />
        <TagsInput label="Ключевые доводы" value={form.key_points}
          onChange={v => setForm({ ...form, key_points: v })}
          description="агент возьмёт ОДИН, уместный для конкретного поста (максимум 5)" />
        <TagsInput label="Красные линии" value={form.red_lines}
          onChange={v => setForm({ ...form, red_lines: v })}
          description="чего агент не скажет никогда (максимум 5)" />
        <Divider />
        <Textarea label="Дополнительный контекст" autosize minRows={2} value={form.worldview}
          onChange={e => setForm({ ...form, worldview: e.currentTarget.value })} />
        <Group grow>
          <Select label="Тактика" data={TACTICS} value={form.tactic}
            onChange={v => setForm({ ...form, tactic: v || 'dynamic' })} />
          <Select label="Режим" data={[
            { value: 'comment', label: 'комментарии' }, { value: 'reply', label: 'ответы' },
            { value: 'mixed', label: 'смешанный' }]}
            value={form.mode} onChange={v => setForm({ ...form, mode: v || 'comment' })} />
          <Select label="Статус" data={[{ value: 'active', label: 'активна' }, { value: 'paused', label: 'на паузе' }]}
            value={form.status} onChange={v => setForm({ ...form, status: v || 'active' })} />
        </Group>
        <MultiSelect label="Агенты миссии" searchable
          data={personas.map(p => ({ value: String(p.id), label: `${p.codename} (${p.caste})` }))}
          value={form.agent_ids} onChange={v => setForm({ ...form, agent_ids: v })}
          placeholder="выберите ИИ-личности" />
        <MultiSelect label="Каналы (scope)" searchable
          data={channels.map(c => ({ value: String(c.id), label: `${c.username} — ${c.title}` }))}
          value={form.channel_ids} onChange={v => setForm({ ...form, channel_ids: v })} />

        {mission && (
          <>
            <Divider label="Запуск в симуляции" />
            <Group grow align="flex-end">
              <Select label="Пост" searchable clearable
                data={posts.map(p => ({ value: String(p.id), label: `#${p.id} ${(p.text || '').slice(0, 40)}` }))}
                value={run.post_id || null} onChange={v => setRun({ ...run, post_id: v || '' })}
                description="пусто — возьмётся свежий пост из scope" />
              <NumberInput label="Реплик на агента" min={1} max={20} value={run.per_agent}
                onChange={v => setRun({ ...run, per_agent: Number(v) || 1 })} />
              <NumberInput label="Пауза, сек" min={0} max={30} value={run.pace_sec}
                onChange={v => setRun({ ...run, pace_sec: Number(v) || 0 })} />
            </Group>
            <SegmentedControl fullWidth value={run.mode} onChange={v => setRun({ ...run, mode: v })}
              data={[
                { value: 'generate', label: 'Только генерация' },
                { value: 'generate_publish', label: 'Генерация + публикация' },
                { value: 'draft', label: 'Черновики' }]} />
            <Button loading={busy} leftSection={<Play size={15} />} onClick={launch}
              disabled={form.status !== 'active' || form.agent_ids.length === 0}>
              Запустить миссию ({form.agent_ids.length} агентов × {run.per_agent})
            </Button>
            {form.status !== 'active' && <Text size="xs" c="dimmed">Миссия на паузе — сначала активируйте.</Text>}
          </>
        )}

        <Group justify="space-between" mt="sm">
          {mission
            ? <Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить</Button>
            : <span />}
          <Group>
            <Button variant="default" onClick={onClose}>Отмена</Button>
            <Button onClick={save} leftSection={<Save size={15} />}>Сохранить</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Compact list of the world's missions with quick create/edit entry points. */
export function MissionsListModal({ opened, onClose, missions, onCreate, onEdit }: {
  opened: boolean; onClose: () => void; missions: SimMission[];
  onCreate: () => void; onEdit: (m: SimMission) => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} size="lg" centered title="Миссии симуляции">
      <Stack gap="xs">
        <Button leftSection={<Play size={15} />} onClick={onCreate}>Создать миссию</Button>
        {missions.length === 0 && <Text c="dimmed" size="sm">Миссий пока нет.</Text>}
        {missions.map(m => (
          <Paper key={m.id} withBorder p="sm" radius="sm" style={{ cursor: 'pointer' }} onClick={() => onEdit(m)}>
            <Group justify="space-between" wrap="nowrap">
              <div style={{ minWidth: 0 }}>
                <Group gap={6}>
                  <Text fw={600} truncate>{m.title}</Text>
                  <Badge size="sm" color={m.status === 'active' ? 'teal' : 'gray'}>
                    {m.status === 'active' ? 'активна' : 'пауза'}
                  </Badge>
                </Group>
                <Text size="xs" c="dimmed" lineClamp={1}>{m.goal || 'без цели'}</Text>
              </div>
              <Group gap={4} wrap="nowrap">
                {m.agents.map(a => <Badge key={a.id} size="xs" color={a.color} variant="light">{a.codename}</Badge>)}
                <Badge size="xs" variant="outline">{m.comments_produced} реплик</Badge>
              </Group>
            </Group>
          </Paper>
        ))}
      </Stack>
    </Modal>
  );
}
