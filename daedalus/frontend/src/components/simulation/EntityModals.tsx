/**
 * Simulation entity editors — channel, post, account, comment.
 * Every field of every entity is editable here (text, author, media, reactions,
 * time, status), which is the polygon's "ручное редактирование любого состояния"
 * requirement. Posts additionally keep a revision history with restore.
 */
import { useEffect, useState } from 'react';
import {
  ActionIcon, Alert, Badge, Button, Code, Divider, Group, Modal, NumberInput, Paper,
  ScrollArea, Select, Stack, Switch, TagsInput, Text, TextInput, Textarea, Tabs,
} from '@mantine/core';
import { Plus, Trash2, RotateCcw, Save } from 'lucide-react';
import {
  SimAccount, SimApi, SimChannel, SimComment, SimMedia, SimPersona, SimPost,
  REACTION_SET, STATUS_LABEL, fromLocalInput, toLocalInput,
} from './api';

const COLORS = ['indigo', 'blue', 'teal', 'green', 'lime', 'yellow', 'orange', 'red', 'pink', 'grape', 'violet', 'cyan', 'gray'];
const MEDIA_KINDS = [
  { value: 'image', label: 'Изображение' }, { value: 'video', label: 'Видео' },
  { value: 'audio', label: 'Аудио' }, { value: 'document', label: 'Документ' },
  { value: 'link', label: 'Ссылка' },
];

function useError() {
  const [error, setError] = useState('');
  const guard = async (fn: () => Promise<void>) => {
    setError('');
    try { await fn(); } catch (e: any) { setError(e.message || 'Ошибка'); }
  };
  return { error, setError, guard };
}

/** Reusable editor for a {emoji: count} map — used by posts and comments. */
export function ReactionEditor({ value, onChange }: { value: Record<string, number>; onChange: (v: Record<string, number>) => void }) {
  const [emoji, setEmoji] = useState('');
  return (
    <Stack gap={6}>
      <Text size="sm">Реакции</Text>
      <Group gap={6}>
        {Object.entries(value).map(([e, n]) => (
          <Badge key={e} size="lg" variant="light" rightSection={
            <ActionIcon size="xs" variant="transparent" color="red" onClick={() => {
              const next = { ...value }; delete next[e]; onChange(next);
            }}><Trash2 size={11} /></ActionIcon>
          }>
            <span style={{ cursor: 'pointer' }} onClick={() => onChange({ ...value, [e]: n + 1 })}>{e} {n}</span>
          </Badge>
        ))}
        {Object.keys(value).length === 0 && <Text size="xs" c="dimmed">пока нет</Text>}
      </Group>
      <Group gap={6}>
        {REACTION_SET.map(e => (
          <ActionIcon key={e} variant="default" size="md" title={`Добавить ${e}`}
            onClick={() => onChange({ ...value, [e]: (value[e] || 0) + 1 })}>{e}</ActionIcon>
        ))}
        <TextInput size="xs" w={90} placeholder="свой" value={emoji}
          onChange={e => setEmoji(e.currentTarget.value)} />
        <Button size="xs" variant="default" disabled={!emoji.trim()}
          onClick={() => { onChange({ ...value, [emoji.trim()]: (value[emoji.trim()] || 0) + 1 }); setEmoji(''); }}>
          Добавить
        </Button>
      </Group>
    </Stack>
  );
}

/** Editor for a post's attachment list (image / video / audio / document / link). */
function MediaEditor({ value, onChange }: { value: SimMedia[]; onChange: (v: SimMedia[]) => void }) {
  const patch = (i: number, key: keyof SimMedia, v: string) =>
    onChange(value.map((m, idx) => (idx === i ? { ...m, [key]: v } : m)));
  return (
    <Stack gap={8}>
      <Group justify="space-between">
        <Text size="sm">Вложения ({value.length})</Text>
        <Button size="xs" variant="default" leftSection={<Plus size={13} />}
          onClick={() => onChange([...value, { kind: 'image', url: '', name: '', caption: '' }])}>
          Добавить вложение
        </Button>
      </Group>
      {value.map((m, i) => (
        <Paper key={i} withBorder p="xs" radius="sm">
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <Select w={140} size="xs" label="Тип" data={MEDIA_KINDS} value={m.kind}
              onChange={v => patch(i, 'kind', v || 'image')} />
            <TextInput style={{ flex: 1 }} size="xs" label="URL / файл" value={m.url || ''}
              onChange={e => patch(i, 'url', e.currentTarget.value)} placeholder="https://… или имя файла" />
            <TextInput style={{ flex: 1 }} size="xs" label="Подпись" value={m.caption || ''}
              onChange={e => patch(i, 'caption', e.currentTarget.value)}
              placeholder="что на фото / о чём аудио — это читает агент" />
            <ActionIcon color="red" variant="light" onClick={() => onChange(value.filter((_, idx) => idx !== i))}>
              <Trash2 size={14} />
            </ActionIcon>
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}

// ── Channel ────────────────────────────────────────────────────────────────

export function ChannelModal({ opened, onClose, api, worldId, channel, onSaved }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number;
  channel: SimChannel | null; onSaved: () => void;
}) {
  const [form, setForm] = useState({
    username: '', title: '', description: '', geo_label: '', subscribers: 0,
    avatar_color: 'indigo', tags: [] as string[],
  });
  const { error, guard } = useError();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!opened) return;
    setForm(channel ? {
      username: channel.username, title: channel.title, description: channel.description || '',
      geo_label: channel.geo_label || '', subscribers: channel.subscribers,
      avatar_color: channel.avatar_color, tags: channel.tags || [],
    } : { username: '', title: '', description: '', geo_label: '', subscribers: 0, avatar_color: 'indigo', tags: [] });
  }, [opened, channel]);

  const save = () => guard(async () => {
    setSaving(true);
    try {
      const body = { ...form, world_id: worldId };
      if (channel) await api.updateChannel(channel.id, body);
      else await api.createChannel(body);
      onSaved(); onClose();
    } finally { setSaving(false); }
  });

  const remove = () => guard(async () => {
    if (!channel || !confirm(`Удалить канал ${channel.username} со всеми постами?`)) return;
    await api.deleteChannel(channel.id);
    onSaved(); onClose();
  });

  return (
    <Modal opened={opened} onClose={onClose} title={channel ? `Канал ${channel.username}` : 'Новый канал'} size="lg" centered>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Group grow>
          <TextInput label="@username" required value={form.username}
            onChange={e => setForm({ ...form, username: e.currentTarget.value })} placeholder="@city_news" />
          <TextInput label="Название" required value={form.title}
            onChange={e => setForm({ ...form, title: e.currentTarget.value })} />
        </Group>
        <Textarea label="Описание" autosize minRows={2} value={form.description}
          onChange={e => setForm({ ...form, description: e.currentTarget.value })} />
        <Group grow>
          <TextInput label="География" value={form.geo_label}
            onChange={e => setForm({ ...form, geo_label: e.currentTarget.value })} placeholder="Ташкент" />
          <NumberInput label="Подписчики" value={form.subscribers} min={0}
            onChange={v => setForm({ ...form, subscribers: Number(v) || 0 })} />
          <Select label="Цвет" data={COLORS} value={form.avatar_color}
            onChange={v => setForm({ ...form, avatar_color: v || 'indigo' })} />
        </Group>
        <TagsInput label="Темы канала" value={form.tags} onChange={v => setForm({ ...form, tags: v })}
          placeholder="Enter, чтобы добавить" />
        <Group justify="space-between" mt="sm">
          {channel
            ? <Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить</Button>
            : <span />}
          <Group>
            <Button variant="default" onClick={onClose}>Отмена</Button>
            <Button loading={saving} onClick={save} leftSection={<Save size={15} />}>Сохранить</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}

// ── Post ───────────────────────────────────────────────────────────────────

export function PostModal({ opened, onClose, api, channels, post, defaultChannelId, personas, onSaved }: {
  opened: boolean; onClose: () => void; api: SimApi; channels: SimChannel[];
  post: SimPost | null; defaultChannelId?: number; personas?: SimPersona[]; onSaved: () => void;
}) {
  const [form, setForm] = useState({
    channel_id: defaultChannelId || 0, text: '', media: [] as SimMedia[],
    reactions: {} as Record<string, number>, views: 0, pinned: false,
    author_label: '', published_at: '', note: '',
  });
  const [revisions, setRevisions] = useState<any[]>([]);
  const { error, guard } = useError();
  const [saving, setSaving] = useState(false);
  // AI-written post/article: the material the polygon's agents will argue under.
  const [gen, setGen] = useState({ persona_id: '', kind: 'post', topic: '', busy: false });

  useEffect(() => {
    if (!opened) return;
    setForm(post ? {
      channel_id: post.channel_id, text: post.text, media: post.media || [],
      reactions: post.reactions || {}, views: post.views, pinned: post.pinned,
      author_label: post.author_label || '', published_at: toLocalInput(post.published_at), note: '',
    } : {
      channel_id: defaultChannelId || channels[0]?.id || 0, text: '', media: [], reactions: {},
      views: 0, pinned: false, author_label: '', published_at: toLocalInput(new Date().toISOString()), note: '',
    });
    if (post) api.revisions(post.id).then(r => setRevisions(r.revisions)).catch(() => setRevisions([]));
    else setRevisions([]);
  }, [opened, post, defaultChannelId]);

  const save = () => guard(async () => {
    setSaving(true);
    try {
      const body: any = {
        channel_id: form.channel_id, text: form.text, media: form.media,
        reactions: form.reactions, views: form.views, pinned: form.pinned,
        author_label: form.author_label || null,
        published_at: fromLocalInput(form.published_at),
      };
      if (post) { body.note = form.note || null; await api.updatePost(post.id, body); }
      else await api.createPost(body);
      onSaved(); onClose();
    } finally { setSaving(false); }
  });

  const remove = () => guard(async () => {
    if (!post || !confirm('Удалить пост со всеми комментариями?')) return;
    await api.deletePost(post.id); onSaved(); onClose();
  });

  const restore = (revId: number) => guard(async () => {
    if (!post) return;
    await api.restoreRevision(post.id, revId);
    const fresh = await api.thread(post.id);
    setForm(f => ({
      ...f, text: fresh.post.text, media: fresh.post.media, reactions: fresh.post.reactions,
      views: fresh.post.views, pinned: fresh.post.pinned,
      author_label: fresh.post.author_label || '', published_at: toLocalInput(fresh.post.published_at),
    }));
    setRevisions((await api.revisions(post.id)).revisions);
    onSaved();
  });

  return (
    <Modal opened={opened} onClose={onClose} size="xl" centered
      title={post ? `Пост #${post.id}` : 'Новый пост'}>
      <Tabs defaultValue="content">
        <Tabs.List mb="sm">
          <Tabs.Tab value="content">Содержимое</Tabs.Tab>
          <Tabs.Tab value="meta">Параметры</Tabs.Tab>
          {post && <Tabs.Tab value="history">История ({revisions.length})</Tabs.Tab>}
        </Tabs.List>

        {error && <Alert color="red" mb="sm">{error}</Alert>}

        <Tabs.Panel value="content">
          <Stack gap="sm">
            <Select label="Канал (автор поста)" required searchable
              data={channels.map(c => ({ value: String(c.id), label: `${c.username} — ${c.title}` }))}
              value={form.channel_id ? String(form.channel_id) : null}
              onChange={v => setForm({ ...form, channel_id: Number(v) })}
              description="Пост принадлежит каналу, а не аккаунту. Смена канала переносит пост." />
            <Textarea label="Текст поста" autosize minRows={5} value={form.text}
              onChange={e => setForm({ ...form, text: e.currentTarget.value })} />

            <Paper withBorder p="xs" radius="sm">
              <Text size="xs" c="dimmed" mb={6}>Сгенерировать текст поста или статьи (ИИ)</Text>
              <Group gap="xs" align="flex-end" wrap="nowrap">
                <Select size="xs" w={140} label="Автор" clearable placeholder="редакция"
                  data={(personas || []).map(p => ({ value: String(p.id), label: p.codename }))}
                  value={gen.persona_id || null} onChange={v => setGen({ ...gen, persona_id: v || '' })} />
                <Select size="xs" w={110} label="Формат" data={[
                  { value: 'post', label: 'пост' }, { value: 'article', label: 'статья' }]}
                  value={gen.kind} onChange={v => setGen({ ...gen, kind: v || 'post' })} />
                <TextInput size="xs" style={{ flex: 1 }} label="Тема" value={gen.topic}
                  onChange={e => setGen({ ...gen, topic: e.currentTarget.value })}
                  placeholder="о чём написать" />
                <Button size="xs" loading={gen.busy} disabled={!form.channel_id}
                  onClick={() => guard(async () => {
                    setGen(g => ({ ...g, busy: true }));
                    try {
                      const r = await api.generatePost({
                        channel_id: form.channel_id, kind: gen.kind, topic: gen.topic || null,
                        persona_id: gen.persona_id ? Number(gen.persona_id) : null, create: false,
                      });
                      if (r.status === 'ok') setForm(f => ({ ...f, text: r.text }));
                      else throw new Error(r.reason || 'Не удалось сгенерировать текст');
                    } finally { setGen(g => ({ ...g, busy: false })); }
                  })}>
                  Сгенерировать
                </Button>
              </Group>
            </Paper>

            <MediaEditor value={form.media} onChange={m => setForm({ ...form, media: m })} />
            <ReactionEditor value={form.reactions} onChange={r => setForm({ ...form, reactions: r })} />
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="meta">
          <Stack gap="sm">
            <Group grow>
              <NumberInput label="Просмотры" value={form.views} min={0}
                onChange={v => setForm({ ...form, views: Number(v) || 0 })} />
              <TextInput label="Время публикации" type="datetime-local" value={form.published_at}
                onChange={e => setForm({ ...form, published_at: e.currentTarget.value })} />
            </Group>
            <TextInput label="Подпись автора (необязательно)" value={form.author_label}
              onChange={e => setForm({ ...form, author_label: e.currentTarget.value })}
              placeholder="по умолчанию — название канала" />
            <Switch label="Закреплён" checked={form.pinned}
              onChange={e => setForm({ ...form, pinned: e.currentTarget.checked })} />
            {post && <TextInput label="Комментарий к правке (в историю)" value={form.note}
              onChange={e => setForm({ ...form, note: e.currentTarget.value })} />}
          </Stack>
        </Tabs.Panel>

        {post && (
          <Tabs.Panel value="history">
            <ScrollArea h={340}>
              <Stack gap="xs">
                {revisions.length === 0 && <Text c="dimmed" size="sm">Правок пока не было.</Text>}
                {revisions.map(r => (
                  <Paper key={r.id} withBorder p="xs" radius="sm">
                    <Group justify="space-between" align="flex-start">
                      <div style={{ flex: 1 }}>
                        <Text size="xs" c="dimmed">
                          {new Date(r.created_at).toLocaleString('ru-RU')} · {r.author || 'оператор'}
                          {r.note ? ` · ${r.note}` : ''}
                        </Text>
                        <Text size="sm" lineClamp={2}>{r.snapshot?.text || '(пусто)'}</Text>
                      </div>
                      <Button size="xs" variant="light" leftSection={<RotateCcw size={13} />}
                        onClick={() => restore(r.id)}>Откатить</Button>
                    </Group>
                  </Paper>
                ))}
              </Stack>
            </ScrollArea>
          </Tabs.Panel>
        )}
      </Tabs>

      <Group justify="space-between" mt="md">
        {post
          ? <Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить пост</Button>
          : <span />}
        <Group>
          <Button variant="default" onClick={onClose}>Отмена</Button>
          <Button loading={saving} onClick={save} leftSection={<Save size={15} />}>Сохранить</Button>
        </Group>
      </Group>
    </Modal>
  );
}

// ── Account (manual executor) ──────────────────────────────────────────────

export function AccountModal({ opened, onClose, api, worldId, account, onSaved }: {
  opened: boolean; onClose: () => void; api: SimApi; worldId: number;
  account: SimAccount | null; onSaved: () => void;
}) {
  const [form, setForm] = useState({
    handle: '', display_name: '', initials: '', color: 'blue', status: 'active', description: '',
  });
  const { error, guard } = useError();

  useEffect(() => {
    if (!opened) return;
    setForm(account ? {
      handle: account.handle, display_name: account.display_name, initials: account.initials,
      color: account.color, status: account.status, description: account.description || '',
    } : { handle: '', display_name: '', initials: '', color: 'blue', status: 'active', description: '' });
  }, [opened, account]);

  const save = () => guard(async () => {
    const body = { ...form, world_id: worldId };
    if (account) await api.updateAccount(account.id, body); else await api.createAccount(body);
    onSaved(); onClose();
  });

  const remove = () => guard(async () => {
    if (!account || !confirm(`Удалить аккаунт ${account.handle}?`)) return;
    await api.deleteAccount(account.id); onSaved(); onClose();
  });

  return (
    <Modal opened={opened} onClose={onClose} centered size="md"
      title={account ? `Аккаунт ${account.handle}` : 'Новый аккаунт'}>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="xs" c="dimmed">
          Аккаунт — ручной исполнитель: им управляет оператор, ИИ его не ведёт. Нужен для массовки и атмосферы.
        </Text>
        <Group grow>
          <TextInput label="@handle" required value={form.handle}
            onChange={e => setForm({ ...form, handle: e.currentTarget.value })} />
          <TextInput label="Имя" required value={form.display_name}
            onChange={e => setForm({ ...form, display_name: e.currentTarget.value })} />
        </Group>
        <Group grow>
          <TextInput label="Инициалы" maxLength={4} value={form.initials}
            onChange={e => setForm({ ...form, initials: e.currentTarget.value })} placeholder="авто" />
          <Select label="Цвет" data={COLORS} value={form.color}
            onChange={v => setForm({ ...form, color: v || 'blue' })} />
          <Select label="Статус" data={[
            { value: 'active', label: 'активен' }, { value: 'muted', label: 'приглушён' },
            { value: 'banned', label: 'заблокирован' }]}
            value={form.status} onChange={v => setForm({ ...form, status: v || 'active' })} />
        </Group>
        <Textarea label="Описание" autosize minRows={2} value={form.description}
          onChange={e => setForm({ ...form, description: e.currentTarget.value })} />
        <Group justify="space-between" mt="sm">
          {account
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

// ── Comment ────────────────────────────────────────────────────────────────

export function CommentModal({ opened, onClose, api, comment, accounts, personas, onSaved }: {
  opened: boolean; onClose: () => void; api: SimApi; comment: SimComment | null;
  accounts: SimAccount[]; personas: SimPersona[]; onSaved: () => void;
}) {
  const [form, setForm] = useState({
    text: '', author_kind: 'account', account_id: null as number | null,
    persona_id: null as number | null, author_label: '', status: 'published',
    reactions: {} as Record<string, number>, published_at: '',
  });
  const { error, guard } = useError();

  useEffect(() => {
    if (!opened || !comment) return;
    setForm({
      text: comment.text, author_kind: comment.author_kind, account_id: comment.account_id,
      persona_id: comment.persona_id, author_label: comment.author_label || '',
      status: comment.status, reactions: comment.reactions || {},
      published_at: toLocalInput(comment.published_at),
    });
  }, [opened, comment]);

  if (!comment) return null;
  const meta = comment.meta || {};

  const save = () => guard(async () => {
    await api.updateComment(comment.id, {
      text: form.text, author_kind: form.author_kind,
      account_id: form.author_kind === 'account' ? form.account_id : null,
      persona_id: form.author_kind === 'persona' ? form.persona_id : null,
      author_label: form.author_kind === 'external' ? (form.author_label || 'аноним') : null,
      status: form.status, reactions: form.reactions,
      published_at: fromLocalInput(form.published_at),
    });
    onSaved(); onClose();
  });

  const remove = () => guard(async () => {
    if (!confirm('Удалить комментарий вместе с ответами на него?')) return;
    await api.deleteComment(comment.id); onSaved(); onClose();
  });

  return (
    <Modal opened={opened} onClose={onClose} size="lg" centered title={`Комментарий #${comment.id}`}>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea label="Текст" autosize minRows={3} value={form.text}
          onChange={e => setForm({ ...form, text: e.currentTarget.value })} />
        <Group grow>
          <Select label="Тип автора" data={[
            { value: 'account', label: 'Аккаунт (ручной)' },
            { value: 'persona', label: 'Агент (ИИ)' },
            { value: 'external', label: 'Внешний голос' }]}
            value={form.author_kind} onChange={v => setForm({ ...form, author_kind: v || 'account' })} />
          {form.author_kind === 'account' && (
            <Select label="Аккаунт" searchable data={accounts.map(a => ({ value: String(a.id), label: `${a.display_name} ${a.handle}` }))}
              value={form.account_id ? String(form.account_id) : null}
              onChange={v => setForm({ ...form, account_id: v ? Number(v) : null })} />
          )}
          {form.author_kind === 'persona' && (
            <Select label="Агент" searchable data={personas.map(p => ({ value: String(p.id), label: `${p.codename} (${p.caste})` }))}
              value={form.persona_id ? String(form.persona_id) : null}
              onChange={v => setForm({ ...form, persona_id: v ? Number(v) : null })} />
          )}
          {form.author_kind === 'external' && (
            <TextInput label="Имя" value={form.author_label}
              onChange={e => setForm({ ...form, author_label: e.currentTarget.value })} />
          )}
        </Group>
        <Group grow>
          <Select label="Статус" data={Object.entries(STATUS_LABEL)
            .filter(([k]) => ['draft', 'generated', 'published', 'scheduled', 'error'].includes(k))
            .map(([value, label]) => ({ value, label }))}
            value={form.status} onChange={v => setForm({ ...form, status: v || 'published' })} />
          <TextInput label="Время" type="datetime-local" value={form.published_at}
            onChange={e => setForm({ ...form, published_at: e.currentTarget.value })} />
        </Group>
        <ReactionEditor value={form.reactions} onChange={r => setForm({ ...form, reactions: r })} />

        {(meta.prompt || meta.reason || meta.rag) && (
          <>
            <Divider label="Трассировка генерации" />
            <Stack gap={6}>
              <Group gap="xs">
                {meta.model && <Badge variant="light">модель: {meta.model}</Badge>}
                {meta.tactic && <Badge variant="light" color="grape">тактика: {meta.tactic}</Badge>}
                {meta.guardrail && <Badge variant="light" color={meta.guardrail === 'passed' ? 'teal' : 'orange'}>
                  guardrails: {meta.guardrail === 'passed' ? 'пройдены' : 'НЕ пройдены'}</Badge>}
              </Group>
              {meta.reason && <Alert color="orange" p="xs"><Text size="xs">{meta.reason}</Text></Alert>}
              {Array.isArray(meta.rag) && meta.rag.length > 0 && (
                <Paper withBorder p="xs" radius="sm">
                  <Text size="xs" c="dimmed" mb={4}>Факты (RAG), поданные в промпт:</Text>
                  {meta.rag.map((f: any, i: number) => <Text key={i} size="xs">• {f.title ? `${f.title}: ` : ''}{f.content}</Text>)}
                </Paper>
              )}
              {meta.prompt && (
                <ScrollArea h={160}>
                  <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 11 }}>{meta.prompt}</Code>
                </ScrollArea>
              )}
            </Stack>
          </>
        )}

        <Group justify="space-between" mt="sm">
          <Button color="red" variant="light" leftSection={<Trash2 size={15} />} onClick={remove}>Удалить</Button>
          <Group>
            <Button variant="default" onClick={onClose}>Отмена</Button>
            <Button onClick={save} leftSection={<Save size={15} />}>Сохранить</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
