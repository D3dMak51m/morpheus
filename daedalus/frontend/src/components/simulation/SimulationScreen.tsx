/**
 * СИМУЛЯЦИЯ — isolated Telegram-like test polygon.
 *
 * Layout (per the operator's mock):
 *   left   — лента активностей + фильтры
 *   centre — посты выбранного канала; при открытии комментариев переключается
 *            в режим одного поста с деревом обсуждения
 *   right  — каналы (поиск/создание/редактирование) + действия + инспектор
 *
 * Nothing on this screen can reach production: every request goes to
 * /api/v1/simulation/* and every artefact lives in the sim_* namespace.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Badge, Box, Button, Group, Select, Text, Title, Tooltip, ActionIcon,
} from '@mantine/core';
import { FlaskConical, RefreshCw, Sprout, Trash2, Plus } from 'lucide-react';
import {
  SimAccount, SimApi, SimChannel, SimComment, SimEvent, SimMission, SimPersona, SimPost, SimState,
} from './api';
import ActivityFeed from './ActivityFeed';
import ChannelFeed from './ChannelFeed';
import ThreadView from './ThreadView';
import RightPanel, { InspectTarget } from './RightPanel';
import { AccountModal, ChannelModal, CommentModal, PostModal } from './EntityModals';
import { MissionModal, MissionsListModal, PersonaModal } from './AgentModals';
import { Booting, KnowledgeModal, LandscapeModal, MassGenModal } from './ToolModals';

interface Props {
  token: string;
  /** #/simulation/<postId> — deep-links straight into a thread. */
  selectedId: string | null;
  onOpen: (id: string) => void;
  onBack: () => void;
}

export default function SimulationScreen({ token, selectedId, onOpen, onBack }: Props) {
  const api = useMemo(() => new SimApi(token), [token]);

  const [state, setState] = useState<SimState | null>(null);
  const [posts, setPosts] = useState<SimPost[]>([]);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [thread, setThread] = useState<{ post: SimPost; comments: SimComment[] } | null>(null);
  const [channelId, setChannelId] = useState<number | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [notice, setNotice] = useState<{ msg: string; error?: boolean } | null>(null);
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  const [loading, setLoading] = useState(true);

  // modal targets (null = create new)
  const [channelModal, setChannelModal] = useState<{ open: boolean; target: SimChannel | null }>({ open: false, target: null });
  const [postModal, setPostModal] = useState<{ open: boolean; target: SimPost | null }>({ open: false, target: null });
  const [accountModal, setAccountModal] = useState<{ open: boolean; target: SimAccount | null }>({ open: false, target: null });
  const [personaModal, setPersonaModal] = useState<{ open: boolean; target: SimPersona | null }>({ open: false, target: null });
  const [missionModal, setMissionModal] = useState<{ open: boolean; target: SimMission | null }>({ open: false, target: null });
  const [commentModal, setCommentModal] = useState<{ open: boolean; target: SimComment | null }>({ open: false, target: null });
  const [missionsOpen, setMissionsOpen] = useState(false);
  const [massGenOpen, setMassGenOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [landscapeOpen, setLandscapeOpen] = useState(false);

  const worldId = state?.world.id ?? 0;
  const channel = state?.channels.find(c => c.id === channelId) || null;

  const say = (msg: string, error = false) => setNotice({ msg, error });

  // ── Loaders ──
  const loadState = useCallback(async (wid?: number) => {
    try {
      const s = await api.state(wid);
      setState(s);
      setChannelId(prev => {
        if (prev && s.channels.some(c => c.id === prev)) return prev;
        return s.channels[0]?.id ?? null;
      });
      return s;
    } catch (e: any) { say(e.message, true); return null; }
  }, [api]);

  const loadPosts = useCallback(async (wid: number, cid: number | null) => {
    if (!wid) return;
    try { setPosts((await api.posts(wid, cid ?? undefined)).posts); } catch (e: any) { say(e.message, true); }
  }, [api]);

  const loadEvents = useCallback(async (wid: number) => {
    if (!wid) return;
    try {
      setEvents((await api.events(wid, {
        kind: kinds.join(',') || undefined,
        status: statuses.join(',') || undefined,
      })).events);
    } catch { /* the feed is best-effort */ }
  }, [api, kinds, statuses]);

  const loadThread = useCallback(async (postId: number) => {
    try { setThread(await api.thread(postId)); } catch (e: any) { say(e.message, true); setThread(null); }
  }, [api]);

  // Initial boot.
  useEffect(() => {
    (async () => {
      setLoading(true);
      await loadState();
      setLoading(false);
    })();
  }, [loadState]);

  useEffect(() => { if (worldId) loadPosts(worldId, channelId); }, [worldId, channelId, loadPosts]);

  useEffect(() => {
    if (!worldId) return;
    loadEvents(worldId);
    const t = setInterval(() => loadEvents(worldId), 5000);
    return () => clearInterval(t);
  }, [worldId, loadEvents]);

  // Thread mode follows the URL hash (#/simulation/<postId>).
  useEffect(() => {
    const postId = selectedId ? Number(selectedId) : NaN;
    if (!Number.isFinite(postId)) { setThread(null); return; }
    loadThread(postId);
  }, [selectedId, loadThread]);

  // While a thread is open, poll it so batch results appear live.
  useEffect(() => {
    if (!thread) return;
    const id = thread.post.id;
    const t = setInterval(() => loadThread(id), 4000);
    return () => clearInterval(t);
  }, [thread?.post.id, loadThread]);

  const refreshAll = useCallback(async () => {
    const s = await loadState(worldId || undefined);
    const wid = s?.world.id ?? worldId;
    await loadPosts(wid, channelId);
    await loadEvents(wid);
    if (thread) await loadThread(thread.post.id);
  }, [loadState, loadPosts, loadEvents, loadThread, worldId, channelId, thread]);

  // ── Actions ──
  const openThread = (post: SimPost) => { setInspect({ entity: 'post', id: post.id, label: `#${post.id}` }); onOpen(String(post.id)); };
  const closeThread = () => { setThread(null); onBack(); };

  const deletePost = async (post: SimPost) => {
    if (!confirm('Удалить пост со всеми комментариями?')) return;
    try {
      await api.deletePost(post.id);
      if (thread?.post.id === post.id) closeThread();
      refreshAll();
      say('Пост удалён.');
    } catch (e: any) { say(e.message, true); }
  };

  const reactPost = async (post: SimPost, emoji: string) => {
    try {
      await api.reactPost(post.id, emoji);
      loadPosts(worldId, channelId);
      if (thread?.post.id === post.id) loadThread(post.id);
    } catch (e: any) { say(e.message, true); }
  };

  const quickComment = async ({ authorKind, id, text }: { authorKind: 'account' | 'persona'; id: number; text: string }) => {
    if (!thread) { say('Сначала откройте пост.', true); return; }
    try {
      await api.createComment({
        post_id: thread.post.id, author_kind: authorKind,
        account_id: authorKind === 'account' ? id : null,
        persona_id: authorKind === 'persona' ? id : null,
        text, status: 'published', origin: 'manual',
      });
      loadThread(thread.post.id);
      say('Комментарий добавлен.');
    } catch (e: any) { say(e.message, true); }
  };

  const seed = async () => {
    try { const r = await api.seedWorld(worldId); await refreshAll(); say(`Демо-среда создана: ${r.created.channels} канала, ${r.created.posts} поста.`); }
    catch (e: any) { say(e.message, true); }
  };

  const reset = async () => {
    if (!confirm('Очистить полигон полностью? Каналы, посты, комментарии, агенты и знания будут удалены.')) return;
    try { await api.resetWorld(worldId); closeThread(); await refreshAll(); say('Полигон очищен.'); }
    catch (e: any) { say(e.message, true); }
  };

  const newWorld = async () => {
    const name = prompt('Название новой симуляции:');
    if (!name) return;
    try { const w = await api.createWorld({ name }); await loadState(w.id); say(`Создана симуляция «${name}».`); }
    catch (e: any) { say(e.message, true); }
  };

  const clearEvents = async () => {
    try { await api.clearEvents(worldId); loadEvents(worldId); } catch (e: any) { say(e.message, true); }
  };

  if (loading || !state) return <Booting />;

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* ── Header ── */}
      <Group justify="space-between" px="md" py="xs" wrap="nowrap"
        style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}>
        <Group gap="sm" wrap="nowrap">
          <FlaskConical size={20} />
          <div>
            <Title order={4} style={{ lineHeight: 1.1 }}>Симуляция</Title>
            <Text size="xs" c="dimmed">Изолированный полигон — боевые каналы, аккаунты и миссии не затрагиваются</Text>
          </div>
        </Group>
        <Group gap={6} wrap="nowrap">
          <Badge size="sm" variant="light" color={state.engine.available ? 'teal' : 'red'}>
            движок: {state.engine.available ? 'на связи' : 'недоступен'}
          </Badge>
          <Select size="xs" w={180} data={state.worlds.map(w => ({ value: String(w.id), label: w.name }))}
            value={String(worldId)} onChange={v => { if (v) { setThread(null); onBack(); loadState(Number(v)); } }} />
          <Tooltip label="Новая симуляция">
            <ActionIcon variant="default" size="md" onClick={newWorld}><Plus size={15} /></ActionIcon>
          </Tooltip>
          <Button size="xs" variant="default" leftSection={<Sprout size={14} />} onClick={seed}>Демо-среда</Button>
          <Button size="xs" variant="default" leftSection={<RefreshCw size={14} />} onClick={refreshAll}>Обновить</Button>
          <Tooltip label="Очистить полигон">
            <ActionIcon variant="default" color="red" size="md" onClick={reset}><Trash2 size={15} /></ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {notice && (
        <Alert color={notice.error ? 'red' : 'teal'} py={6} px="md" radius={0}
          withCloseButton onClose={() => setNotice(null)}>
          <Text size="sm">{notice.msg}</Text>
        </Alert>
      )}

      {/* ── Three columns ── */}
      <Box style={{ flex: 1, display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr) 340px', minHeight: 0 }}>
        <Box style={{ borderRight: '1px solid var(--border-subtle)', minHeight: 0 }}>
          <ActivityFeed
            events={events} kinds={kinds} statuses={statuses}
            onKinds={setKinds} onStatuses={setStatuses}
            onOpenPost={pid => onOpen(String(pid))}
            onRefresh={() => loadEvents(worldId)} onClear={clearEvents}
          />
        </Box>

        <Box style={{ minHeight: 0, background: 'var(--bg-base)' }}>
          {thread ? (
            <ThreadView
              api={api} worldId={worldId} post={thread.post} comments={thread.comments}
              accounts={state.accounts} personas={state.personas} missions={state.missions}
              onBack={closeThread}
              onReload={() => { loadThread(thread.post.id); loadPosts(worldId, channelId); loadEvents(worldId); }}
              onEditPost={() => setPostModal({ open: true, target: thread.post })}
              onEditComment={c => setCommentModal({ open: true, target: c })}
              onMassGen={() => setMassGenOpen(true)}
              onNotice={say}
            />
          ) : (
            <ChannelFeed
              channel={channel} posts={posts}
              onOpenThread={openThread}
              onEditPost={p => setPostModal({ open: true, target: p })}
              onDeletePost={deletePost}
              onNewPost={() => setPostModal({ open: true, target: null })}
              onEditChannel={() => channel && setChannelModal({ open: true, target: channel })}
              onReactPost={reactPost}
            />
          )}
        </Box>

        <Box style={{ borderLeft: '1px solid var(--border-subtle)', minHeight: 0, background: 'var(--bg-surface)' }}>
          <RightPanel
            api={api} channels={state.channels} accounts={state.accounts} personas={state.personas}
            missions={state.missions} selectedChannelId={channelId} activePost={thread?.post || null}
            inspect={inspect}
            onSelectChannel={id => { setChannelId(id); if (thread) closeThread(); }}
            onCreateChannel={() => setChannelModal({ open: true, target: null })}
            onEditChannel={c => setChannelModal({ open: true, target: c })}
            onCreateAccount={() => setAccountModal({ open: true, target: null })}
            onEditAccount={a => setAccountModal({ open: true, target: a })}
            onCreatePersona={() => setPersonaModal({ open: true, target: null })}
            onEditPersona={p => setPersonaModal({ open: true, target: p })}
            onOpenMissions={() => setMissionsOpen(true)}
            onCreateMission={() => setMissionModal({ open: true, target: null })}
            onNewPost={() => setPostModal({ open: true, target: null })}
            onMassGen={() => setMassGenOpen(true)}
            onKnowledge={() => setKnowledgeOpen(true)}
            onLandscape={() => setLandscapeOpen(true)}
            onQuickComment={quickComment}
            onInspect={setInspect}
          />
        </Box>
      </Box>

      {/* ── Modals ── */}
      <ChannelModal opened={channelModal.open} onClose={() => setChannelModal({ open: false, target: null })}
        api={api} worldId={worldId} channel={channelModal.target} onSaved={refreshAll} />

      <PostModal opened={postModal.open} onClose={() => setPostModal({ open: false, target: null })}
        api={api} channels={state.channels} post={postModal.target}
        defaultChannelId={channelId ?? undefined} personas={state.personas} onSaved={refreshAll} />

      <AccountModal opened={accountModal.open} onClose={() => setAccountModal({ open: false, target: null })}
        api={api} worldId={worldId} account={accountModal.target} onSaved={refreshAll} />

      <PersonaModal opened={personaModal.open} onClose={() => setPersonaModal({ open: false, target: null })}
        api={api} worldId={worldId} persona={personaModal.target} onSaved={refreshAll} />

      <MissionModal opened={missionModal.open} onClose={() => setMissionModal({ open: false, target: null })}
        api={api} worldId={worldId} mission={missionModal.target} personas={state.personas}
        channels={state.channels} posts={posts} onSaved={refreshAll}
        onJobStarted={msg => say(msg)} />

      <MissionsListModal opened={missionsOpen} onClose={() => setMissionsOpen(false)}
        missions={state.missions}
        onCreate={() => { setMissionsOpen(false); setMissionModal({ open: true, target: null }); }}
        onEdit={m => { setMissionsOpen(false); setMissionModal({ open: true, target: m }); }} />

      <CommentModal opened={commentModal.open} onClose={() => setCommentModal({ open: false, target: null })}
        api={api} comment={commentModal.target} accounts={state.accounts} personas={state.personas}
        onSaved={() => { if (thread) loadThread(thread.post.id); loadEvents(worldId); }} />

      <MassGenModal opened={massGenOpen} onClose={() => setMassGenOpen(false)}
        api={api} worldId={worldId} post={thread?.post || null} personas={state.personas}
        accounts={state.accounts} missions={state.missions}
        onDone={() => { if (thread) loadThread(thread.post.id); loadEvents(worldId); }} />

      <KnowledgeModal opened={knowledgeOpen} onClose={() => setKnowledgeOpen(false)}
        api={api} worldId={worldId} onChanged={refreshAll} />

      <LandscapeModal opened={landscapeOpen} onClose={() => setLandscapeOpen(false)}
        api={api} worldId={worldId} channels={state.channels} onChanged={refreshAll} />
    </Box>
  );
}
