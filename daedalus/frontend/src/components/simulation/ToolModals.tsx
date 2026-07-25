/**
 * Polygon tools: mass generation, knowledge (RAG) management + import from the
 * production system, and landscape scraping.
 *
 * Mass generation runs several AI agents AND several manual accounts against one
 * post at once, in generate / generate+publish / draft mode, with no production
 * rate limits, cooldowns or active-hours gating.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Badge, Button, Group, Modal, MultiSelect, NumberInput, Paper, Progress, ScrollArea,
  SegmentedControl, Select, Slider, Stack, TagsInput, Text, TextInput, Textarea, Tabs, Loader,
} from '@mantine/core';
import { Play, RefreshCw, Trash2, Download, Globe, Plus, Square } from 'lucide-react';
import {
  SimAccount, SimApi, SimChannel, SimJob, SimKnowledge, SimLandscapeSource, SimMission,
  SimPersona, SimPost, STATUS_COLOR, STATUS_LABEL,
} from './api';

// ── Mass generation ────────────────────────────────────────────────────────

export function MassGenModal({ opened, onClose, api, worldId, post, personas, accounts, missions, onDone }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number; post: SimPost | null;
  personas: SimPersona[]; accounts: SimAccount[]; missions: SimMission[]; onDone: () => void;
}) {
  const [form, setForm] = useState({
    persona_ids: [] as string[], account_ids: [] as string[], account_texts: '',
    count: 5, mode: 'generate_publish', order: 'round_robin', pace_sec: 0, reply_ratio: 0,
    mission_id: '', tone: '', temperature: 0.8, max_tokens: 0, prompt_override: '',
  });
  const [jobs, setJobs] = useState<SimJob[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadJobs = useCallback(async () => {
    try { setJobs((await api.jobs(worldId)).jobs.slice(0, 5)); } catch { /* ignore */ }
  }, [api, worldId]);

  useEffect(() => {
    if (!opened) return;
    setError('');
    loadJobs();
    const t = setInterval(loadJobs, 2000);
    return () => clearInterval(t);
  }, [opened, loadJobs]);

  const start = async () => {
    if (!post) return;
    setBusy(true); setError('');
    try {
      const job = await api.batch({
        world_id: worldId, post_id: post.id,
        mission_id: form.mission_id ? Number(form.mission_id) : null,
        persona_ids: form.persona_ids.map(Number),
        account_ids: form.account_ids.map(Number),
        account_texts: form.account_texts.split('\n').map(s => s.trim()).filter(Boolean),
        count: form.count, mode: form.mode, order: form.order, pace_sec: form.pace_sec,
        reply_ratio: form.reply_ratio / 100,
        tone: form.tone || null, temperature: form.temperature,
        max_tokens: form.max_tokens || null, prompt_override: form.prompt_override || null,
      });
      setJobs(j => [job, ...j]);
      onDone();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const cancel = async (id: number) => {
    try { await api.cancelJob(id); loadJobs(); } catch (e: any) { setError(e.message); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="xl" centered title="Массовая генерация">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        {!post && <Alert color="orange">Сначала выберите пост в центральной колонке.</Alert>}
        {post && (
          <Paper withBorder p="xs" radius="sm">
            <Text size="xs" c="dimmed">Пост #{post.id} · {post.channel?.username}</Text>
            <Text size="sm" lineClamp={2}>{post.text || '(без текста)'}</Text>
          </Paper>
        )}

        <Group grow align="flex-start">
          <MultiSelect label="Агенты (ИИ)" searchable
            data={personas.map(p => ({ value: String(p.id), label: `${p.codename} (${p.caste})` }))}
            value={form.persona_ids} onChange={v => setForm({ ...form, persona_ids: v })}
            description="генерируют текст через ORPHEUS" />
          <MultiSelect label="Аккаунты (ручные)" searchable
            data={accounts.map(a => ({ value: String(a.id), label: `${a.display_name} ${a.handle}` }))}
            value={form.account_ids} onChange={v => setForm({ ...form, account_ids: v })}
            description="публикуют ваши реплики — ИИ их не ведёт" />
        </Group>

        {form.account_ids.length > 0 && (
          <Textarea label="Реплики для ручных аккаунтов (по одной в строке)" autosize minRows={3}
            value={form.account_texts} onChange={e => setForm({ ...form, account_texts: e.currentTarget.value })}
            placeholder={'ну наконец-то\nпосмотрим, что выйдет\nда сколько можно'} />
        )}

        <Group grow>
          <NumberInput label="Количество" min={1} max={200} value={form.count}
            onChange={v => setForm({ ...form, count: Number(v) || 1 })} />
          <Select label="Порядок" data={[
            { value: 'round_robin', label: 'по очереди (агенты+аккаунты)' },
            { value: 'random', label: 'случайно' },
            { value: 'serial', label: 'последовательно' }]}
            value={form.order} onChange={v => setForm({ ...form, order: v || 'round_robin' })} />
          <NumberInput label="Темп (пауза, сек)" min={0} max={30} value={form.pace_sec}
            onChange={v => setForm({ ...form, pace_sec: Number(v) || 0 })} />
          <Select label="Миссия (контекст)" clearable
            data={missions.map(m => ({ value: String(m.id), label: m.title }))}
            value={form.mission_id || null} onChange={v => setForm({ ...form, mission_id: v || '' })} />
        </Group>

        <Stack gap={2}>
          <Text size="sm">Доля ответов на существующие комментарии: {form.reply_ratio}%</Text>
          <Slider value={form.reply_ratio} onChange={v => setForm({ ...form, reply_ratio: v })}
            min={0} max={100} step={10}
            marks={[{ value: 0, label: 'новые' }, { value: 50, label: 'поровну' }, { value: 100, label: 'ответы' }]}
            mb="lg" />
        </Stack>

        <SegmentedControl fullWidth value={form.mode} onChange={v => setForm({ ...form, mode: v })}
          data={[
            { value: 'generate', label: 'Только генерация' },
            { value: 'generate_publish', label: 'Генерация + публикация' },
            { value: 'draft', label: 'Черновики' }]} />

        <Group grow>
          <TextInput label="Тональность" value={form.tone}
            onChange={e => setForm({ ...form, tone: e.currentTarget.value })}
            placeholder="напр. раздражённо, но без грубости" />
          <NumberInput label="Температура" min={0} max={2} step={0.1} decimalScale={1}
            value={form.temperature} onChange={v => setForm({ ...form, temperature: Number(v) || 0.8 })} />
          <NumberInput label="Лимит токенов (0 — без лимита)" min={0} max={2000}
            value={form.max_tokens} onChange={v => setForm({ ...form, max_tokens: Number(v) || 0 })} />
        </Group>

        <Textarea label="Переопределение промпта (для теста системных промптов)" autosize minRows={2}
          value={form.prompt_override} onChange={e => setForm({ ...form, prompt_override: e.currentTarget.value })}
          placeholder="Плейсхолдеры: {persona} {post} {channel} {thread} {knowledge} {goal} {stance} {rules}" />

        <Button loading={busy} disabled={!post || (form.persona_ids.length === 0 && form.account_ids.length === 0)}
          leftSection={<Play size={16} />} onClick={start}>
          Запустить генерацию ({form.count})
        </Button>

        {jobs.length > 0 && (
          <Stack gap={6}>
            <Text size="sm" fw={600}>Задания</Text>
            {jobs.map(j => (
              <Paper key={j.id} withBorder p="xs" radius="sm">
                <Group justify="space-between" mb={4}>
                  <Group gap={6}>
                    <Badge size="sm" color={STATUS_COLOR[j.status] || 'gray'}>{STATUS_LABEL[j.status] || j.status}</Badge>
                    <Text size="xs" c="dimmed">#{j.id} · {j.mode} · {j.done + j.failed}/{j.total}</Text>
                  </Group>
                  {(j.status === 'running' || j.status === 'queued') && (
                    <Button size="compact-xs" variant="light" color="red" leftSection={<Square size={11} />}
                      onClick={() => cancel(j.id)}>Стоп</Button>
                  )}
                </Group>
                <Progress value={j.total ? ((j.done + j.failed) / j.total) * 100 : 0}
                  color={j.failed ? 'orange' : 'teal'} size="sm" />
                {j.message && <Text size="xs" c="dimmed" mt={4}>{j.message}</Text>}
              </Paper>
            ))}
          </Stack>
        )}
      </Stack>
    </Modal>
  );
}

// ── Knowledge (RAG) ────────────────────────────────────────────────────────

const KNOWLEDGE_KINDS = [
  { value: 'fact', label: 'факт' }, { value: 'news', label: 'новость' },
  { value: 'rule', label: 'системное правило' }, { value: 'prompt', label: 'промпт' },
  { value: 'channel_profile', label: 'профиль канала' }, { value: 'history', label: 'история' },
];

export function KnowledgeModal({ opened, onClose, api, worldId, onChanged }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number; onChanged: () => void;
}) {
  const [rows, setRows] = useState<SimKnowledge[]>([]);
  const [form, setForm] = useState({ kind: 'fact', title: '', content: '', tags: [] as string[], source: '' });
  const [imp, setImp] = useState({ what: 'knowledge', limit: 50, query: '', layers: [] as string[] });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setRows((await api.knowledge(worldId)).knowledge); } catch (e: any) { setError(e.message); }
  }, [api, worldId]);

  useEffect(() => { if (opened) { setError(''); setMessage(''); load(); } }, [opened, load]);

  const add = async () => {
    if (!form.content.trim()) { setError('Введите текст.'); return; }
    setError('');
    try {
      await api.createKnowledge({ ...form, world_id: worldId });
      setForm({ kind: form.kind, title: '', content: '', tags: [], source: '' });
      load(); onChanged();
    } catch (e: any) { setError(e.message); }
  };

  const remove = async (id: number) => {
    try { await api.deleteKnowledge(id); load(); onChanged(); } catch (e: any) { setError(e.message); }
  };

  const runImport = async () => {
    setBusy(true); setError(''); setMessage('');
    try {
      const r = await api.importKnowledge({
        world_id: worldId, what: imp.what, limit: imp.limit,
        query: imp.query || null, layers: imp.layers,
      });
      setMessage(r.message);
      load(); onChanged();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="xl" centered title="Знания симуляции (RAG)">
      <Tabs defaultValue="base">
        <Tabs.List mb="sm">
          <Tabs.Tab value="base">База ({rows.length})</Tabs.Tab>
          <Tabs.Tab value="add">Добавить</Tabs.Tab>
          <Tabs.Tab value="import">Импорт из системы</Tabs.Tab>
        </Tabs.List>
        {error && <Alert color="red" mb="sm">{error}</Alert>}
        {message && <Alert color="teal" mb="sm">{message}</Alert>}

        <Tabs.Panel value="base">
          <ScrollArea h={380}>
            <Stack gap={6}>
              {rows.length === 0 && <Text c="dimmed" size="sm">База знаний пуста — добавьте факт или импортируйте.</Text>}
              {rows.map(k => (
                <Paper key={k.id} withBorder p="xs" radius="sm">
                  <Group justify="space-between" align="flex-start" wrap="nowrap">
                    <div style={{ minWidth: 0 }}>
                      <Group gap={4} mb={2}>
                        <Badge size="xs" variant="light">{k.kind}</Badge>
                        <Badge size="xs" variant="outline" color="gray">{k.origin}</Badge>
                        {(k.tags || []).slice(0, 4).map(t => <Badge key={t} size="xs" variant="dot">{t}</Badge>)}
                      </Group>
                      <Text size="sm">{k.title ? <b>{k.title}: </b> : null}{k.content}</Text>
                    </div>
                    <Button size="compact-xs" color="red" variant="subtle" onClick={() => remove(k.id)}>
                      <Trash2 size={13} />
                    </Button>
                  </Group>
                </Paper>
              ))}
            </Stack>
          </ScrollArea>
        </Tabs.Panel>

        <Tabs.Panel value="add">
          <Stack gap="sm">
            <Group grow>
              <Select label="Тип" data={KNOWLEDGE_KINDS} value={form.kind}
                onChange={v => setForm({ ...form, kind: v || 'fact' })} />
              <TextInput label="Заголовок" value={form.title}
                onChange={e => setForm({ ...form, title: e.currentTarget.value })} />
            </Group>
            <Textarea label="Содержимое" required autosize minRows={4} value={form.content}
              onChange={e => setForm({ ...form, content: e.currentTarget.value })} />
            <Group grow>
              <TagsInput label="Теги" value={form.tags} onChange={v => setForm({ ...form, tags: v })} />
              <TextInput label="Источник" value={form.source}
                onChange={e => setForm({ ...form, source: e.currentTarget.value })} />
            </Group>
            <Button leftSection={<Plus size={15} />} onClick={add}>Добавить в базу полигона</Button>
            <Text size="xs" c="dimmed">
              Правила и промпты (типы «системное правило» / «промпт») подставляются в КАЖДУЮ генерацию,
              остальные типы попадают в промпт только если релевантны посту.
            </Text>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="import">
          <Stack gap="sm">
            <Alert color="blue" p="xs">
              <Text size="xs">Импорт только читает боевые таблицы и копирует данные в полигон. Ничего в
                боевой системе не меняется.</Text>
            </Alert>
            <Select label="Что импортировать" data={[
              { value: 'knowledge', label: 'Факты из базы знаний роя' },
              { value: 'channel_profiles', label: 'Профили каналов (+ создать каналы)' },
              { value: 'landscape', label: 'Источники ландшафта' },
              { value: 'souls', label: 'Души → агенты полигона' },
              { value: 'missions', label: 'Миссии → сценарии полигона' },
              { value: 'history', label: 'История: реальные реплики роя' }]}
              value={imp.what} onChange={v => setImp({ ...imp, what: v || 'knowledge' })} />
            {imp.what === 'history' && (
              <NumberInput label="Сколько реплик" min={1} max={500} value={imp.limit}
                onChange={v => setImp({ ...imp, limit: Number(v) || 50 })} />
            )}
            {imp.what === 'knowledge' && (
              <Group grow>
                <NumberInput label="Сколько фактов" min={1} max={500} value={imp.limit}
                  onChange={v => setImp({ ...imp, limit: Number(v) || 50 })} />
                <TextInput label="Фильтр по тексту" value={imp.query}
                  onChange={e => setImp({ ...imp, query: e.currentTarget.value })} />
                <MultiSelect label="Слои" data={['global', 'regional', 'state', 'city', 'personal']}
                  value={imp.layers} onChange={v => setImp({ ...imp, layers: v })} />
              </Group>
            )}
            <Button loading={busy} leftSection={<Download size={15} />} onClick={runImport}>
              Импортировать в полигон
            </Button>
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}

// ── Landscape scraping ─────────────────────────────────────────────────────

export function LandscapeModal({ opened, onClose, api, worldId, channels, onChanged }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number;
  channels: SimChannel[]; onChanged: () => void;
}) {
  const [form, setForm] = useState({
    kind: 'rss', url: '', limit: 20, as_knowledge: false, target_channel_id: '',
    tags: [] as string[], save_source: true,
  });
  const [sources, setSources] = useState<SimLandscapeSource[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setSources((await api.landscape(worldId)).sources); } catch { /* ignore */ }
  }, [api, worldId]);

  useEffect(() => { if (opened) { setError(''); setMessage(''); load(); } }, [opened, load]);

  const scrape = async () => {
    if (!form.url.trim()) { setError('Укажите адрес источника.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const r = await api.scrape({
        world_id: worldId, kind: form.kind, url: form.url.trim(), limit: form.limit,
        as_knowledge: form.as_knowledge, tags: form.tags, save_source: form.save_source,
        target_channel_id: form.target_channel_id ? Number(form.target_channel_id) : null,
      });
      if (r.status === 'ok') { setMessage(r.message); onChanged(); load(); }
      else setError(r.message || 'Не удалось загрузить источник.');
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const runSource = async (id: number) => {
    setBusy(true); setError(''); setMessage('');
    try {
      const r = await api.runLandscape(id);
      if (r.status === 'ok') { setMessage(r.message); onChanged(); }
      else setError(r.message);
      load();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="lg" centered title="Ландшафт — наполнение полигона">
      <Tabs defaultValue="scrape">
        <Tabs.List mb="sm">
          <Tabs.Tab value="scrape">Скрапинг</Tabs.Tab>
          <Tabs.Tab value="sources">Источники ({sources.length})</Tabs.Tab>
        </Tabs.List>
        {error && <Alert color="red" mb="sm">{error}</Alert>}
        {message && <Alert color="teal" mb="sm">{message}</Alert>}

        <Tabs.Panel value="scrape">
          <Stack gap="sm">
            <Select label="Тип источника" data={[
              { value: 'rss', label: 'RSS / Atom лента' },
              { value: 'web', label: 'Веб-страница (статья)' },
              { value: 'tg_preview', label: 'Публичный Telegram-канал (веб-превью, только чтение)' }]}
              value={form.kind} onChange={v => setForm({ ...form, kind: v || 'rss' })} />
            <TextInput label="Адрес" required value={form.url}
              onChange={e => setForm({ ...form, url: e.currentTarget.value })}
              placeholder={form.kind === 'tg_preview' ? '@tashkent_news333' : 'https://example.org/feed'} />
            <Group grow>
              <NumberInput label="Сколько записей" min={1} max={100} value={form.limit}
                onChange={v => setForm({ ...form, limit: Number(v) || 20 })} />
              <Select label="Куда складывать" data={[
                { value: 'posts', label: 'Посты в канал полигона' },
                { value: 'knowledge', label: 'В базу знаний (RAG)' }]}
                value={form.as_knowledge ? 'knowledge' : 'posts'}
                onChange={v => setForm({ ...form, as_knowledge: v === 'knowledge' })} />
              {!form.as_knowledge && (
                <Select label="Канал (пусто — создать новый)" clearable searchable
                  data={channels.map(c => ({ value: String(c.id), label: `${c.username} — ${c.title}` }))}
                  value={form.target_channel_id || null}
                  onChange={v => setForm({ ...form, target_channel_id: v || '' })} />
              )}
            </Group>
            <TagsInput label="Теги" value={form.tags} onChange={v => setForm({ ...form, tags: v })} />
            <Button loading={busy} leftSection={<Globe size={15} />} onClick={scrape}>
              Загрузить в полигон
            </Button>
            <Text size="xs" c="dimmed">
              Скрапинг только читает открытые источники (для Telegram — публичное веб-превью t.me/s/…,
              без входа в аккаунт) и складывает материал в изолированный полигон.
            </Text>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="sources">
          <ScrollArea h={340}>
            <Stack gap={6}>
              {sources.length === 0 && <Text c="dimmed" size="sm">Источников пока нет.</Text>}
              {sources.map(s => (
                <Paper key={s.id} withBorder p="xs" radius="sm">
                  <Group justify="space-between" wrap="nowrap">
                    <div style={{ minWidth: 0 }}>
                      <Group gap={4}>
                        <Badge size="xs" variant="light">{s.kind}</Badge>
                        {s.last_status && <Badge size="xs" color={s.last_status === 'ok' ? 'teal' : 'red'}>
                          {s.last_status === 'ok' ? 'ок' : 'ошибка'}</Badge>}
                        <Text size="xs" c="dimmed">{s.items_imported} записей</Text>
                      </Group>
                      <Text size="sm" truncate>{s.title || s.url}</Text>
                      {s.last_message && <Text size="xs" c="dimmed" lineClamp={1}>{s.last_message}</Text>}
                    </div>
                    <Group gap={4} wrap="nowrap">
                      <Button size="compact-xs" variant="light" leftSection={<RefreshCw size={12} />}
                        loading={busy} onClick={() => runSource(s.id)}>Запустить</Button>
                      <Button size="compact-xs" variant="subtle" color="red"
                        onClick={async () => { await api.deleteLandscape(s.id); load(); }}>
                        <Trash2 size={13} />
                      </Button>
                    </Group>
                  </Group>
                </Paper>
              ))}
            </Stack>
          </ScrollArea>
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}

/** Small spinner used while the polygon boots. */
export function Booting() {
  return (
    <Group justify="center" p="xl"><Loader size="sm" /><Text c="dimmed">Загрузка полигона…</Text></Group>
  );
}
