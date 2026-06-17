import { useState, useEffect, useCallback } from 'react';
import { Users, History, Link, Unlink, Radio, RefreshCw } from 'lucide-react';
import { DataTable, Column } from './DataTable';
import './AccountsManager.css';
import ChannelManager from './ChannelManager';

interface Account {
  id: number;
  agent_id: string | null;
  platform: string;
  username: string;
  status: string;
  device_id: string | null;
}

interface AuditLog {
  id: number;
  action: string;
  timestamp: string;
}

interface SoulProfile {
  agent_id: string;
  codename: string;
  full_name: string;
  status: string;
}

export default function AccountsManager({ token }: { token: string | null }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profiles, setProfiles] = useState<SoulProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [bindAgentId, setBindAgentId] = useState('');
  const [channelAgent, setChannelAgent] = useState<{ agentId: string; label: string } | null>(null);

  const fetchAccounts = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const res = await fetch('/api/v1/souls/accounts', {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) setAccounts(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [token]);

  const fetchProfiles = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch('/api/v1/souls/profiles', {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) setProfiles(await res.json());
    } catch (e) {
      console.error(e);
    }
  }, [token]);

  const fetchLogs = async (accountId: number) => {
    if (!token) return;
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/history`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) setLogs(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchAccounts();
    fetchProfiles();
  }, [fetchAccounts, fetchProfiles]);

  useEffect(() => {
    if (selectedAccount) {
      fetchLogs(selectedAccount.id);
      setBindAgentId('');
    }
  }, [selectedAccount]);

  const refreshAfterMutation = async (accountId: number) => {
    await fetchAccounts();
    await fetchProfiles();
    fetchLogs(accountId);
    // Keep the detail pane in sync with the mutated row.
    try {
      const res = await fetch('/api/v1/souls/accounts', { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const fresh: Account[] = await res.json();
        const updated = fresh.find(a => a.id === accountId);
        if (updated) setSelectedAccount(updated);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleBind = async (accountId: number, agentId: string) => {
    if (!agentId) return;
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/bind?agent_id=${encodeURIComponent(agentId)}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) refreshAfterMutation(accountId);
    } catch (e) {
      console.error(e);
    }
  };

  const handleUnbind = async (accountId: number) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/unbind`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) refreshAfterMutation(accountId);
    } catch (e) {
      console.error(e);
    }
  };

  const columns: Column<Account>[] = [
    { key: 'username', header: 'Аккаунт',
      render: a => <span className="am-username">{a.username}</span> },
    { key: 'platform', header: 'Платформа', width: '120px',
      render: a => <span className="platform-badge">{a.platform}</span> },
    { key: 'status', header: 'Статус', width: '120px',
      render: a => <span className={`status-badge ${a.status}`}>{a.status}</span> },
    { key: 'agent_id', header: 'Привязка', sortValue: a => a.agent_id || '',
      render: a => a.agent_id
        ? <span className="assigned"><Link size={12} /> {a.agent_id}</span>
        : <span className="unassigned"><Unlink size={12} /> Свободен</span> },
  ];

  return (
    <div className="accounts-manager view-container">
      <div className="header-row">
        <div>
          <h1><Users size={22} style={{ verticalAlign: '-4px' }} /> Аккаунты и привязки</h1>
          <p className="subtitle">Реальные TG-аккаунты роя и их привязка к персонам («душам»). Выберите аккаунт, чтобы привязать/отвязать душу, посмотреть каналы и историю.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={() => { fetchAccounts(); fetchProfiles(); }}>
            <RefreshCw size={14} /> Обновить
          </button>
        </div>
      </div>

      <div className="accounts-layout">
        <div className="accounts-list">
          <DataTable
            columns={columns}
            rows={accounts}
            rowKey={a => a.id}
            loading={loading}
            searchText={a => `${a.username} ${a.agent_id || ''} ${a.platform} ${a.status}`}
            searchPlaceholder="🔍 Поиск по имени, платформе, статусу или привязке…"
            emptyText="Аккаунтов нет. Добавьте их через «Фабрику авторизации»."
            pageSize={25}
            onRowClick={setSelectedAccount}
          />
        </div>

        {selectedAccount && (
          <div className="account-details">
            <h3>Детали аккаунта</h3>
            <div className="detail-box">
              <p><strong>Платформа:</strong> {selectedAccount.platform}</p>
              <p><strong>Имя:</strong> {selectedAccount.username}</p>
              <p><strong>Устройство:</strong> {selectedAccount.device_id || 'Нет'}</p>
              <p><strong>Статус:</strong> <span className={`status-badge ${selectedAccount.status}`}>{selectedAccount.status}</span></p>

              <div className="assignment-control">
                <h4>Привязка души</h4>
                {selectedAccount.agent_id ? (
                  <div className="assigned-view">
                    <p>Привязан к душе <strong>{selectedAccount.agent_id}</strong></p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button onClick={() => handleUnbind(selectedAccount.id)} className="btn-danger">
                        <Unlink size={16} /> Отвязать душу
                      </button>
                      {selectedAccount.platform === 'telegram' && (
                        <button
                          className="btn-secondary"
                          onClick={() => setChannelAgent({ agentId: selectedAccount.agent_id!, label: selectedAccount.username })}
                        >
                          <Radio size={16} /> Каналы аккаунта
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="assign-form">
                    <select value={bindAgentId} onChange={e => setBindAgentId(e.target.value)}>
                      <option value="">Выберите свободную душу…</option>
                      {profiles.map(p => (
                        <option key={p.agent_id} value={p.agent_id}>
                          {p.full_name || p.codename} ({p.agent_id}) · {p.status}
                        </option>
                      ))}
                    </select>
                    <button onClick={() => handleBind(selectedAccount.id, bindAgentId)} className="btn-primary" disabled={!bindAgentId}>
                      <Link size={16} /> Привязать душу
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="audit-logs">
              <h3><History size={18} /> История изменений</h3>
              {logs.length === 0 ? <p className="text-muted">История пуста.</p> : (
                <ul>
                  {logs.map(log => (
                    <li key={log.id}>
                      <span className="time">{new Date(log.timestamp).toLocaleString('ru-RU')}</span>
                      <span className="action">{log.action}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>

      {channelAgent && token && (
        <ChannelManager token={token} agentId={channelAgent.agentId} label={channelAgent.label} onClose={() => setChannelAgent(null)} />
      )}
    </div>
  );
}
