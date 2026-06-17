import React, { useEffect, useRef, useState } from 'react';
import { Factory, Cpu, Bot, Rocket, CheckCircle2, XCircle } from 'lucide-react';
import './CloneFactory.css';

interface CloneFactoryProps {
  token: string;
}

interface BotState {
  index: number;
  stage: string;
  status: string;
  agent_id: string | null;
  device_id: string | null;
  phone: string | null;
  account_id: number | null;
  error: string | null;
}

interface Job {
  job_id: string;
  status: string;
  params: { count: number; caste: string; target_platform: string; vector_focus: string };
  log: string[];
  bots: BotState[];
  summary?: { bound: number; failed: number; total: number };
}

const CASTES = ['alpha', 'beta', 'gamma'];
const PLATFORMS = ['instagram', 'telegram', 'twitter', 'threads', 'youtube'];

// Pipeline steps for the per-bot progress stepper.
const STEPS = ['Персона', 'Регистрация', 'Привязка', 'Готово'];
const STAGE_TO_STEP: Record<string, number> = {
  queued: 0,
  generating_persona: 0,
  registering: 1,
  binding: 2,
  bound: 3,
  failed: -1,
};

const STAGE_LABEL: Record<string, string> = {
  queued: 'В очереди',
  generating_persona: 'Генерация персоны…',
  registering: 'Регистрация · SMS/OTP…',
  binding: 'Привязка к душе…',
  bound: 'Привязан ✓',
  failed: 'Сбой',
};

const CloneFactory: React.FC<CloneFactoryProps> = ({ token }) => {
  const [count, setCount] = useState(5);
  const [caste, setCaste] = useState('beta');
  const [platform, setPlatform] = useState('instagram');
  const [vectorFocus, setVectorFocus] = useState('');
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState('');
  const [job, setJob] = useState<Job | null>(null);

  const pollRef = useRef<number | null>(null);
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const pollJob = (jobId: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/factory/jobs/${jobId}`, { headers });
        if (res.ok) {
          const data: Job = await res.json();
          setJob(data);
          if (data.status === 'completed' && pollRef.current) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch (e) {
        console.error(e);
      }
    }, 2000);
  };

  const handleLaunch = async () => {
    if (!vectorFocus.trim()) { setError('Укажите вектор фокуса.'); return; }
    setLaunching(true);
    setError('');
    try {
      const res = await fetch('/api/v1/factory/mass-provision', {
        method: 'POST',
        headers,
        body: JSON.stringify({ count, caste, target_platform: platform, vector_focus: vectorFocus }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setJob({ job_id: data.job_id, status: data.status, params: { count, caste, target_platform: platform, vector_focus: vectorFocus }, log: [], bots: data.bots });
      pollJob(data.job_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось запустить фабрику');
    } finally {
      setLaunching(false);
    }
  };

  const jobRunning = job && job.status !== 'completed';

  return (
    <div className="clone-factory view-container">
      <div className="header-row">
        <div>
          <h1><Factory size={22} style={{ verticalAlign: '-4px' }} /> Фабрика клонов</h1>
          <p className="subtitle">Автономное массовое создание ботов — запуск AVD, синтез душ, регистрация аккаунтов и привязка, без ручного участия. (Мобильный стек вне scope.)</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* ── Provision form ── */}
      <div className="cf-builder">
        <div className="cf-field">
          <div className="slider-head">
            <label>Число ботов</label>
            <input
              type="number" className="slider-num" min={1} max={20} value={count}
              onChange={e => setCount(Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
              disabled={!!jobRunning}
            />
          </div>
          <input
            type="range" className="styled-range" min={1} max={20} value={count}
            style={{ ['--pct' as string]: `${((count - 1) / 19) * 100}%` }}
            onChange={e => setCount(parseInt(e.target.value))} disabled={!!jobRunning}
          />
          <div className="cf-slider-ends"><span>1</span><span>20</span></div>
        </div>
        <div className="cf-row">
          <div className="cf-field">
            <label>Каста</label>
            <select value={caste} onChange={e => setCaste(e.target.value)} disabled={!!jobRunning}>
              {CASTES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="cf-field">
            <label>Платформа</label>
            <select value={platform} onChange={e => setPlatform(e.target.value)} disabled={!!jobRunning}>
              {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        </div>
        <div className="cf-field">
          <label>Вектор фокуса</label>
          <input value={vectorFocus} onChange={e => setVectorFocus(e.target.value)} disabled={!!jobRunning}
            placeholder="напр. гражданский активист, Ташкент, городское развитие" />
        </div>
        <button className="btn-primary cf-launch" onClick={handleLaunch} disabled={launching || !!jobRunning}>
          <Rocket size={16} /> {jobRunning ? 'Создание…' : launching ? 'Запуск…' : `Создать ${count} ${count > 1 ? 'ботов' : 'бота'}`}
        </button>
      </div>

      {/* ── Execution monitor ── */}
      {job && (
        <div className="cf-monitor">
          <div className="cf-monitor-head">
            <h2><Cpu size={18} /> Монитор выполнения</h2>
            <span className={`cf-job-status ${job.status}`}>{job.status.replace(/_/g, ' ')}</span>
            {job.summary && (
              <span className="cf-summary">{job.summary.bound} привязано · {job.summary.failed} сбой / {job.summary.total}</span>
            )}
          </div>

          <div className="cf-grid">
            {job.bots.map(bot => {
              const step = STAGE_TO_STEP[bot.stage] ?? 0;
              const failed = bot.stage === 'failed' || bot.status === 'failed';
              const done = bot.stage === 'bound' || bot.status === 'done';
              return (
                <div key={bot.index} className={`cf-bot ${failed ? 'failed' : done ? 'done' : 'active'}`}>
                  <div className="cf-bot-head">
                    <span className="cf-bot-title"><Bot size={14} /> Бот {bot.index}</span>
                    {done && <CheckCircle2 size={16} className="cf-ok" />}
                    {failed && <XCircle size={16} className="cf-bad" />}
                  </div>

                  <div className="cf-steps">
                    {STEPS.map((label, i) => (
                      <div key={label} className={`cf-step ${failed && i === step + 1 ? 'failed' : i <= step ? 'done' : ''} ${i === step && !done && !failed ? 'current' : ''}`}>
                        <span className="cf-step-dot" />
                        <span className="cf-step-label">{label}</span>
                      </div>
                    ))}
                  </div>

                  <div className="cf-bot-meta">
                    <div className="cf-stage-label">{STAGE_LABEL[bot.stage] || bot.stage}</div>
                    {bot.agent_id && <div className="cf-meta-row">душа: <span>{bot.agent_id}</span></div>}
                    {bot.device_id && <div className="cf-meta-row">avd: <span>{bot.device_id}</span></div>}
                    {bot.phone && <div className="cf-meta-row">телефон: <span>{bot.phone}</span></div>}
                    {bot.error && <div className="cf-error">{bot.error}</div>}
                  </div>
                </div>
              );
            })}
          </div>

          {job.log.length > 0 && (
            <div className="cf-log">
              <h3>Журнал фабрики</h3>
              {job.log.map((line, i) => <div key={i} className="cf-log-line">{line}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default CloneFactory;
