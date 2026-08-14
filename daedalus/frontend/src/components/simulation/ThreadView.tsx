/**
 * Centre column — thread mode: ONE post plus its Telegram-like comment tree.
 * Opening comments switches the centre area into this mode (single post + tree +
 * replies + reactions + publication controls + author pickers + mass generation),
 * exactly as the layout requires.
 */
import { useState } from 'react';
import {
  ActionIcon, Alert, Avatar, Badge, Box, Button, Code, Collapse, Group, Paper, ScrollArea,
  SegmentedControl, Select, Stack, Text, Textarea, Title, Tooltip,
} from '@mantine/core';
import {
  ArrowLeft, Bot, Pencil, Reply, Send, Sparkles, Trash2, Upload, User, Wand2, X, Terminal,
} from 'lucide-react';
import {
  SimAccount, SimApi, SimComment, SimMission, SimPersona, SimPost,
  STATUS_COLOR, STATUS_LABEL, initialsOf, timeAgo,
} from './api';
import { MediaRow, ReactionRow } from './ChannelFeed';

interface Props {
  api: SimApi;
  worldId: number;
  post: SimPost;
  comments: SimComment[];
  accounts: SimAccount[];
  personas: SimPersona[];
  missions: SimMission[];
  onBack: () => void;
  onReload: () => void;
  onEditPost: () => void;
  onEditComment: (c: SimComment) => void;
  onMassGen: () => void;
  onNotice: (msg: string, error?: boolean) => void;
}

interface TreeNode { comment: SimComment; children: TreeNode[] }

function buildTree(comments: SimComment[]): TreeNode[] {
  const nodes = new Map<number, TreeNode>();
  comments.forEach(c => nodes.set(c.id, { comment: c, children: [] }));
  const roots: TreeNode[] = [];
  comments.forEach(c => {
    const node = nodes.get(c.id)!;
    const parent = c.parent_id ? nodes.get(c.parent_id) : undefined;
    if (parent) parent.children.push(node); else roots.push(node);
  });
  return roots;
}

export default function ThreadView({
  api, worldId, post, comments, accounts, personas, missions,
  onBack, onReload, onEditPost, onEditComment, onMassGen, onNotice,
}: Props) {
  const [authorKind, setAuthorKind] = useState<'account' | 'persona'>('account');
  const [accountId, setAccountId] = useState<string | null>(accounts[0] ? String(accounts[0].id) : null);
  const [personaId, setPersonaId] = useState<string | null>(personas[0] ? String(personas[0].id) : null);
  const [missionId, setMissionId] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [replyTo, setReplyTo] = useState<SimComment | null>(null);
  const [genMode, setGenMode] = useState('generate_publish');
  const [busy, setBusy] = useState(false);
  const [showPrompt, setShowPrompt] = useState<number | null>(null);
  const [lastPrompt, setLastPrompt] = useState('');

  const tree = buildTree(comments);
  const published = comments.filter(c => c.status === 'published').length;

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); } catch (e: any) { onNotice(e.message || 'Ошибка', true); } finally { setBusy(false); }
  };

  const sendManual = () => guard(async () => {
    if (!text.trim()) { onNotice('Введите текст комментария.', true); return; }
    if (authorKind === 'account' && !accountId) { onNotice('Выберите аккаунт.', true); return; }
    if (authorKind === 'persona' && !personaId) { onNotice('Выберите агента.', true); return; }
    await api.createComment({
      post_id: post.id, parent_id: replyTo?.id ?? null, author_kind: authorKind,
      account_id: authorKind === 'account' ? Number(accountId) : null,
      persona_id: authorKind === 'persona' ? Number(personaId) : null,
      text: text.trim(), status: 'published', origin: 'manual',
      mission_id: missionId ? Number(missionId) : null,
    });
    setText(''); setReplyTo(null); onReload();
    onNotice('Комментарий отправлен.');
  });

  const generate = () => guard(async () => {
    if (!personaId) { onNotice('Выберите ИИ-агента для генерации.', true); return; }
    const r = await api.generate({
      world_id: worldId, post_id: post.id, persona_id: Number(personaId),
      parent_id: replyTo?.id ?? null, mission_id: missionId ? Number(missionId) : null,
      mode: genMode, prompt_override: text.trim() ? text.trim() : null,
    });
    setLastPrompt(r.prompt || '');
    if (r.status === 'ok') {
      onNotice(`Готово: «${r.comment.text.slice(0, 60)}»`);
      setText(''); setReplyTo(null);
    } else {
      onNotice(`Генерация не удалась: ${r.reason}`, true);
    }
    onReload();
  });

  const renderNode = (node: TreeNode, depth = 0) => {
    const c = node.comment;
    const isAi = c.origin === 'ai';
    const meta = c.meta || {};
    return (
      <Box key={c.id} style={{ marginLeft: Math.min(depth, 6) * 22 }}>
        <Paper withBorder p="xs" radius="md" mb={6}
          style={{
            background: 'var(--bg-surface)',
            borderLeft: depth > 0 ? '2px solid var(--border-subtle)' : undefined,
            opacity: c.status === 'draft' ? 0.75 : 1,
          }}>
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <Group gap="xs" align="flex-start" wrap="nowrap" style={{ minWidth: 0 }}>
              <Avatar size="sm" radius="xl" color={c.author_color}>{initialsOf(c.author_label)}</Avatar>
              <div style={{ minWidth: 0 }}>
                <Group gap={5}>
                  <Text size="sm" fw={600}>{c.author_label}</Text>
                  <Tooltip label={isAi ? 'Сгенерировано ИИ-агентом' : 'Ручное действие оператора'}>
                    <Badge size="xs" variant="light" color={isAi ? 'grape' : 'blue'}
                      leftSection={isAi ? <Bot size={9} /> : <User size={9} />}>
                      {isAi ? 'ИИ' : 'ручной'}
                    </Badge>
                  </Tooltip>
                  <Badge size="xs" color={STATUS_COLOR[c.status] || 'gray'} variant="light">
                    {STATUS_LABEL[c.status] || c.status}
                  </Badge>
                  {c.mission_id && <Badge size="xs" variant="outline" color="teal">миссия #{c.mission_id}</Badge>}
                  {meta.guardrail === 'failed' && <Badge size="xs" color="orange">guardrails ✗</Badge>}
                  <Text size="xs" c="dimmed">{timeAgo(c.published_at || c.created_at)}</Text>
                </Group>
                <Text size="sm" mt={2} style={{ whiteSpace: 'pre-wrap' }}>{c.text}</Text>
                <ReactionRow reactions={c.reactions}
                  onReact={emoji => guard(async () => { await api.reactComment(c.id, emoji); onReload(); })} />
                <Group gap={4} mt={4}>
                  <Button size="compact-xs" variant="subtle" leftSection={<Reply size={12} />}
                    onClick={() => setReplyTo(c)}>Ответить</Button>
                  <Button size="compact-xs" variant="subtle" leftSection={<Pencil size={12} />}
                    onClick={() => onEditComment(c)}>Изменить</Button>
                  {c.status !== 'published' && (
                    <Button size="compact-xs" variant="subtle" color="teal" leftSection={<Upload size={12} />}
                      onClick={() => guard(async () => { await api.publishComment(c.id); onReload(); })}>
                      Опубликовать
                    </Button>
                  )}
                  {meta.prompt && (
                    <Button size="compact-xs" variant="subtle" color="grape" leftSection={<Terminal size={12} />}
                      onClick={() => setShowPrompt(showPrompt === c.id ? null : c.id)}>
                      Промпт
                    </Button>
                  )}
                  <Button size="compact-xs" variant="subtle" color="red" leftSection={<Trash2 size={12} />}
                    onClick={() => guard(async () => {
                      if (!confirm('Удалить комментарий вместе с ответами?')) return;
                      await api.deleteComment(c.id); onReload();
                    })}>Удалить</Button>
                </Group>
                <Collapse in={showPrompt === c.id}>
                  <Stack gap={4} mt={6}>
                    {meta.reason && <Alert color="orange" p={6}><Text size="xs">{meta.reason}</Text></Alert>}
                    {Array.isArray(meta.rag) && meta.rag.length > 0 && (
                      <Paper withBorder p={6} radius="sm">
                        <Text size="xs" c="dimmed">Факты, поданные в промпт:</Text>
                        {meta.rag.map((f: any, i: number) => <Text key={i} size="xs">• {f.content}</Text>)}
                      </Paper>
                    )}
                    <ScrollArea h={180}>
                      <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 11 }}>{meta.prompt}</Code>
                    </ScrollArea>
                  </Stack>
                </Collapse>
              </div>
            </Group>
          </Group>
        </Paper>
        {node.children.map(child => renderNode(child, depth + 1))}
      </Box>
    );
  };

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Group justify="space-between" p="sm" wrap="nowrap"
        style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}>
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          <ActionIcon variant="default" onClick={onBack} title="К ленте канала"><ArrowLeft size={16} /></ActionIcon>
          <div style={{ minWidth: 0 }}>
            <Title order={5} style={{ lineHeight: 1.1 }}>Пост #{post.id} · обсуждение</Title>
            <Text size="xs" c="dimmed" truncate>
              {post.channel?.username} · {comments.length} комментариев ({published} опубликовано)
            </Text>
          </div>
        </Group>
        <Group gap={6} wrap="nowrap">
          <Button size="xs" variant="default" leftSection={<Pencil size={13} />} onClick={onEditPost}>Пост</Button>
          <Button size="xs" variant="light" leftSection={<Sparkles size={14} />} onClick={onMassGen}>Массово</Button>
        </Group>
      </Group>

      <ScrollArea style={{ flex: 1 }} type="hover">
        <Stack gap="sm" p="md">
          {/* The post itself — single-post mode */}
          <Paper withBorder p="sm" radius="md" style={{ background: 'var(--bg-surface)' }}>
            <Group gap="xs" mb={4} wrap="nowrap">
              <Avatar size="sm" radius="xl" color={post.channel?.avatar_color || 'indigo'}>
                {initialsOf(post.channel?.title || '?')}
              </Avatar>
              <div>
                <Text size="sm" fw={600}>{post.author_label || post.channel?.title}</Text>
                <Text size="xs" c="dimmed">{timeAgo(post.published_at)} · {post.views.toLocaleString('ru-RU')} просмотров</Text>
              </div>
            </Group>
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{post.text}</Text>
            <MediaRow media={post.media} />
            <ReactionRow reactions={post.reactions}
              onReact={emoji => guard(async () => { await api.reactPost(post.id, emoji); onReload(); })} />
          </Paper>

          {comments.length === 0 && (
            <Text size="sm" c="dimmed" ta="center" py="md">
              Комментариев нет — напишите вручную или сгенерируйте ниже.
            </Text>
          )}
          {tree.map(node => renderNode(node))}
        </Stack>
      </ScrollArea>

      {/* Composer */}
      <Box p="sm" style={{ borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}>
        {replyTo && (
          <Group gap={6} mb={6}>
            <Badge variant="light" leftSection={<Reply size={11} />}>
              Ответ для {replyTo.author_label}: {replyTo.text.slice(0, 40)}…
            </Badge>
            <ActionIcon size="xs" variant="subtle" onClick={() => setReplyTo(null)}><X size={12} /></ActionIcon>
          </Group>
        )}
        <Group gap="xs" mb={6} wrap="wrap">
          <SegmentedControl size="xs" value={authorKind}
            onChange={v => setAuthorKind(v as 'account' | 'persona')}
            data={[{ value: 'account', label: 'Аккаунт (ручной)' }, { value: 'persona', label: 'Агент (ИИ)' }]} />
          {authorKind === 'account' ? (
            <Select size="xs" w={210} placeholder="аккаунт" searchable
              data={accounts.map(a => ({ value: String(a.id), label: `${a.display_name} ${a.handle}` }))}
              value={accountId} onChange={setAccountId} />
          ) : (
            <Select size="xs" w={210} placeholder="агент" searchable
              data={personas.map(p => ({ value: String(p.id), label: `${p.codename} (${p.caste})` }))}
              value={personaId} onChange={setPersonaId} />
          )}
          <Select size="xs" w={190} placeholder="без миссии" clearable
            data={missions.map(m => ({ value: String(m.id), label: m.title }))}
            value={missionId} onChange={setMissionId} />
          <Select size="xs" w={200} value={genMode} onChange={v => setGenMode(v || 'generate_publish')}
            data={[
              { value: 'generate_publish', label: 'Генерация + публикация' },
              { value: 'generate', label: 'Только генерация' },
              { value: 'draft', label: 'Черновик' }]} />
        </Group>
        <Textarea autosize minRows={2} maxRows={6} value={text}
          onChange={e => setText(e.currentTarget.value)}
          placeholder={authorKind === 'account'
            ? 'Текст комментария от имени аккаунта…'
            : 'Пусто — агент придумает сам. Текст здесь становится переопределением промпта.'} />
        <Group justify="space-between" mt={6}>
          <Text size="xs" c="dimmed">
            {authorKind === 'persona'
              ? 'Агент управляется ИИ: ORPHEUS соберёт промпт из персоны, знаний полигона и ветки.'
              : 'Аккаунт — ручной исполнитель: публикуется ровно ваш текст.'}
          </Text>
          <Group gap={6}>
            {authorKind === 'account' && (
              <Button size="xs" loading={busy} leftSection={<Send size={14} />} onClick={sendManual}>Отправить</Button>
            )}
            {authorKind === 'persona' && (
              <>
                <Button size="xs" variant="default" loading={busy} leftSection={<Send size={14} />}
                  onClick={sendManual} disabled={!text.trim()}>Отправить как есть</Button>
                <Button size="xs" loading={busy} leftSection={<Wand2 size={14} />} onClick={generate}>
                  Сгенерировать
                </Button>
              </>
            )}
          </Group>
        </Group>
        {lastPrompt && (
          <Collapse in={!!lastPrompt}>
            <Text size="xs" c="dimmed" mt={6}>Последний промпт передан агенту — открыть можно кнопкой «Промпт» у комментария.</Text>
          </Collapse>
        )}
      </Box>
    </Box>
  );
}
