/**
 * SIMULATION API client — every call goes to /api/v1/simulation/*, the isolated
 * polygon namespace. Nothing here can reach a production endpoint.
 */

export interface SimWorld {
  id: number; name: string; description: string | null; settings: Record<string, any>;
  counts: { channels: number; posts: number; comments: number; accounts: number; personas: number; missions: number; knowledge: number };
}
export interface SimChannel {
  id: number; world_id: number; username: string; title: string; description: string | null;
  avatar_color: string; subscribers: number; geo_label: string | null; tags: string[];
  source: string; external_ref: string | null; posts_count: number; last_post_at: string | null;
}
export interface SimMedia { kind: string; url?: string; name?: string; caption?: string }
export interface SimPost {
  id: number; channel_id: number; text: string; media: SimMedia[]; reactions: Record<string, number>;
  views: number; pinned: boolean; author_label: string | null; source: string;
  external_ref: string | null; published_at: string; created_at: string; updated_at: string;
  channel: { id: number; username: string; title: string; avatar_color: string } | null;
  comments_count?: number; revisions_count?: number;
}
export interface SimComment {
  id: number; post_id: number; parent_id: number | null; author_kind: string;
  account_id: number | null; persona_id: number | null; author_label: string; author_color: string;
  text: string; reactions: Record<string, number>; status: string; origin: string;
  mission_id: number | null; job_id: number | null; meta: Record<string, any>;
  published_at: string | null; created_at: string; updated_at: string;
}
export interface SimAccount {
  id: number; world_id: number; handle: string; display_name: string; initials: string;
  color: string; status: string; description: string | null;
}
export interface SimPersona {
  id: number; world_id: number; agent_key: string; codename: string; full_name: string | null;
  caste: string; status: string; color: string; bio: string | null; core_mission: string | null;
  interests: string[]; style: Record<string, any>; system_prompt: string | null;
  settings: Record<string, any>; source_agent_id: string | null;
}
export interface SimMissionAgent { id: number; persona_id: number; role: string; codename: string; caste: string; color: string }
export interface SimMission {
  id: number; world_id: number; title: string; goal: string | null; stance: string | null;
  worldview: string | null; tactic: string; mode: string; status: string;
  // The explicit position, mirroring production `missions`.
  our_side: string | null; opponent: string | null;
  key_points: string[]; red_lines: string[];
  scope: { channel_ids?: number[]; post_id?: number }; settings: Record<string, any>;
  agents: SimMissionAgent[]; comments_produced: number;
}
export interface SimKnowledge {
  id: number; world_id: number; kind: string; title: string | null; content: string;
  tags: string[]; source: string | null; origin: string; weight: number; created_at: string;
}
export interface SimLandscapeSource {
  id: number; world_id: number; kind: string; url: string; title: string | null;
  target_channel_id: number | null; options: Record<string, any>; last_run_at: string | null;
  last_status: string | null; last_message: string | null; items_imported: number;
}
export interface SimEvent {
  id: number; world_id: number; kind: string; status: string; actor_kind: string | null;
  actor_label: string | null; actor_id: number | null; channel_id: number | null;
  post_id: number | null; comment_id: number | null; mission_id: number | null;
  job_id: number | null; summary: string; detail: Record<string, any>; created_at: string;
}
export interface SimJob {
  id: number; kind: string; mode: string; status: string; params: Record<string, any>;
  post_id: number | null; mission_id: number | null; total: number; done: number;
  failed: number; message: string | null; created_at: string;
}
export interface SimState {
  world: SimWorld; worlds: { id: number; name: string }[]; channels: SimChannel[];
  accounts: SimAccount[]; personas: SimPersona[]; missions: SimMission[];
  engine: { available: boolean };
}

const BASE = '/api/v1/simulation';

export class SimApi {
  constructor(private token: string) {}

  private get headers() {
    return { 'Content-Type': 'application/json', Authorization: `Bearer ${this.token}` };
  }

  private async call<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(BASE + path, { ...init, headers: this.headers });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const body = await r.json(); detail = body.detail || detail; } catch { /* keep status */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return r.status === 204 ? (undefined as T) : r.json();
  }

  private post<T>(path: string, body?: any) {
    return this.call<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
  }
  private put<T>(path: string, body: any) {
    return this.call<T>(path, { method: 'PUT', body: JSON.stringify(body) });
  }
  private del<T>(path: string) { return this.call<T>(path, { method: 'DELETE' }); }

  // ── World ──
  state(worldId?: number) { return this.call<SimState>(`/state${worldId ? `?world_id=${worldId}` : ''}`); }
  createWorld(body: { name: string; description?: string }) { return this.post<SimWorld>('/worlds', body); }
  updateWorld(id: number, body: { name: string; description?: string; settings?: any }) { return this.put<SimWorld>(`/worlds/${id}`, body); }
  deleteWorld(id: number) { return this.del(`/worlds/${id}`); }
  seedWorld(id: number) { return this.post<{ created: Record<string, number> }>(`/worlds/${id}/seed`); }
  resetWorld(id: number) { return this.post<SimWorld>(`/worlds/${id}/reset`); }

  // ── Channels ──
  channels(worldId: number) { return this.call<{ channels: SimChannel[] }>(`/channels?world_id=${worldId}`); }
  createChannel(body: Partial<SimChannel> & { world_id: number }) { return this.post<SimChannel>('/channels', body); }
  updateChannel(id: number, body: Partial<SimChannel>) { return this.put<SimChannel>(`/channels/${id}`, body); }
  deleteChannel(id: number) { return this.del(`/channels/${id}`); }

  // ── Posts ──
  posts(worldId: number, channelId?: number) {
    return this.call<{ posts: SimPost[] }>(`/posts?world_id=${worldId}${channelId ? `&channel_id=${channelId}` : ''}`);
  }
  thread(postId: number) { return this.call<{ post: SimPost; comments: SimComment[] }>(`/posts/${postId}`); }
  createPost(body: any) { return this.post<SimPost>('/posts', body); }
  updatePost(id: number, body: any) { return this.put<SimPost>(`/posts/${id}`, body); }
  deletePost(id: number) { return this.del(`/posts/${id}`); }
  revisions(postId: number) { return this.call<{ revisions: any[] }>(`/posts/${postId}/revisions`); }
  restoreRevision(postId: number, revId: number) { return this.post<SimPost>(`/posts/${postId}/revisions/${revId}/restore`); }
  reactPost(id: number, emoji: string, delta = 1) { return this.post<SimPost>(`/posts/${id}/reactions`, { emoji, delta }); }

  // ── Comments ──
  createComment(body: any) { return this.post<SimComment>('/comments', body); }
  updateComment(id: number, body: any) { return this.put<SimComment>(`/comments/${id}`, body); }
  deleteComment(id: number) { return this.del(`/comments/${id}`); }
  reactComment(id: number, emoji: string, delta = 1) { return this.post<SimComment>(`/comments/${id}/reactions`, { emoji, delta }); }
  publishComment(id: number) { return this.post<SimComment>(`/comments/${id}/publish`); }

  // ── Accounts & personas ──
  createAccount(body: any) { return this.post<SimAccount>('/accounts', body); }
  updateAccount(id: number, body: any) { return this.put<SimAccount>(`/accounts/${id}`, body); }
  deleteAccount(id: number) { return this.del(`/accounts/${id}`); }
  createPersona(body: any) { return this.post<SimPersona>('/personas', body); }
  updatePersona(id: number, body: any) { return this.put<SimPersona>(`/personas/${id}`, body); }
  deletePersona(id: number) { return this.del(`/personas/${id}`); }
  exportPersona(id: number) { return this.call<Record<string, any>>(`/personas/${id}/export`); }

  // ── Missions ──
  createMission(body: any) { return this.post<SimMission>('/missions', body); }
  updateMission(id: number, body: any) { return this.put<SimMission>(`/missions/${id}`, body); }
  deleteMission(id: number) { return this.del(`/missions/${id}`); }
  runMission(id: number, body: any) { return this.post<SimJob>(`/missions/${id}/run`, body); }

  // ── Knowledge & landscape ──
  knowledge(worldId: number, kind?: string) {
    return this.call<{ knowledge: SimKnowledge[] }>(`/knowledge?world_id=${worldId}${kind ? `&kind=${kind}` : ''}`);
  }
  createKnowledge(body: any) { return this.post<SimKnowledge>('/knowledge', body); }
  updateKnowledge(id: number, body: any) { return this.put<SimKnowledge>(`/knowledge/${id}`, body); }
  deleteKnowledge(id: number) { return this.del(`/knowledge/${id}`); }
  importKnowledge(body: any) { return this.post<{ imported: number; message: string }>('/knowledge/import', body); }
  landscape(worldId: number) { return this.call<{ sources: SimLandscapeSource[] }>(`/landscape?world_id=${worldId}`); }
  createLandscape(body: any) { return this.post<SimLandscapeSource>('/landscape', body); }
  deleteLandscape(id: number) { return this.del(`/landscape/${id}`); }
  runLandscape(id: number) { return this.post<any>(`/landscape/${id}/run`); }
  scrape(body: any) { return this.post<any>('/landscape/scrape', body); }
  // Real Telegram threads (posts + the comments real people left under them).
  // The MTProto read happens in MYRMIDON; this is read-only.
  tgImportAgents() {
    return this.call<{ agents: { agent_id: string; codename: string }[] }>('/import/telegram/agents');
  }
  importTelegram(body: any) {
    return this.post<{
      status: string; channel: string; channel_id: number;
      posts_imported: number; posts_seen: number;
      comments_imported: number; comments_seen: number;
    }>('/import/telegram', body);
  }

  // ── Generation ──
  generate(body: any) {
    return this.post<{ status: string; reason: string; comment: SimComment; prompt: string; rag: SimKnowledge[] }>('/generate', body);
  }
  generatePost(body: any) {
    return this.post<{ status: string; reason: string; text: string; prompt: string; post: SimPost | null }>('/generate/post', body);
  }
  batch(body: any) { return this.post<SimJob>('/generate/batch', body); }
  jobs(worldId: number) { return this.call<{ jobs: SimJob[] }>(`/jobs?world_id=${worldId}`); }
  cancelJob(id: number) { return this.post<SimJob>(`/jobs/${id}/cancel`); }

  // ── Activity & inspector ──
  events(worldId: number, filters: { kind?: string; status?: string; post_id?: number } = {}) {
    const q = new URLSearchParams({ world_id: String(worldId) });
    if (filters.kind) q.set('kind', filters.kind);
    if (filters.status) q.set('status', filters.status);
    if (filters.post_id) q.set('post_id', String(filters.post_id));
    return this.call<{ events: SimEvent[] }>(`/events?${q.toString()}`);
  }
  clearEvents(worldId: number) { return this.del(`/events?world_id=${worldId}`); }
  inspect(entity: string, id: number) {
    return this.call<{ entity: string; table: string; data: Record<string, any> }>(`/inspect/${entity}/${id}`);
  }
}

// ── Shared display helpers ──

export const STATUS_COLOR: Record<string, string> = {
  draft: 'gray', generated: 'blue', published: 'teal', scheduled: 'yellow',
  error: 'red', done: 'teal', running: 'blue', queued: 'yellow', cancelled: 'gray',
};
export const STATUS_LABEL: Record<string, string> = {
  draft: 'черновик', generated: 'сгенерировано', published: 'отправлено',
  scheduled: 'запланировано', error: 'ошибка', done: 'завершено',
  running: 'выполняется', queued: 'в очереди', cancelled: 'отменено',
};
export const KIND_LABEL: Record<string, string> = {
  channel: 'канал', post: 'пост', comment: 'комментарий', reaction: 'реакция',
  mission: 'миссия', agent: 'агент', account: 'аккаунт', knowledge: 'знания',
  landscape: 'ландшафт', generation: 'генерация', system: 'система',
};
export const TACTICS = [
  { value: 'dynamic', label: 'Динамическая (по настроению ветки)' },
  { value: 'soft_support', label: 'Мягкая поддержка' },
  { value: 'aggressive_displacement', label: 'Прямое вытеснение' },
  { value: 'sentiment_shift', label: 'Сдвиг настроения' },
  { value: 'amplify', label: 'Усиление' },
];
export const REACTION_SET = ['👍', '👎', '🔥', '❤️', '😂', '😱', '🤔', '💩'];

export function initialsOf(name: string): string {
  const parts = (name || '?').trim().split(/\s+/);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (name || '?').slice(0, 2)).toUpperCase();
}

export function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'только что';
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/** ISO ⇄ value for <input type="datetime-local"> (which wants local time, no zone). */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function fromLocalInput(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}
