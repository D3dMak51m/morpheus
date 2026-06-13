import { useState, useEffect } from 'react';
import { Users, History, Link, Unlink, Radio } from 'lucide-react';
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
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [bindAgentId, setBindAgentId] = useState('');
  const [channelAgent, setChannelAgent] = useState<{ agentId: string; label: string } | null>(null);

  const fetchAccounts = async () => {
    if (!token) return;
    try {
      const res = await fetch('/api/v1/souls/accounts', {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) setAccounts(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchProfiles = async () => {
    if (!token) return;
    try {
      const res = await fetch('/api/v1/souls/profiles', {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) setProfiles(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

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
  }, [token]);

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

  return (
    <div className="accounts-manager">
      <div className="header-panel">
        <Users size={24} />
        <h2>Accounts & Identity Vault</h2>
      </div>

      <div className="accounts-layout">
        <div className="accounts-list">
          <div className="list-header">
            <h3>Registered Accounts</h3>
          </div>
          <div className="grid">
            {accounts.map(acc => (
              <div 
                key={acc.id} 
                className={`account-card ${selectedAccount?.id === acc.id ? 'selected' : ''}`}
                onClick={() => setSelectedAccount(acc)}
              >
                <div className="acc-header">
                  <span className="platform-badge">{acc.platform}</span>
                  <span className={`status-dot ${acc.status}`} />
                </div>
                <h4>{acc.username}</h4>
                <div className="assignment-status">
                  {acc.agent_id ? (
                    <span className="assigned"><Link size={12}/> {acc.agent_id}</span>
                  ) : (
                    <span className="unassigned"><Unlink size={12}/> Unassigned</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {selectedAccount && (
          <div className="account-details">
            <h3>Account Details</h3>
            <div className="detail-box">
              <p><strong>Platform:</strong> {selectedAccount.platform}</p>
              <p><strong>Username:</strong> {selectedAccount.username}</p>
              <p><strong>Device:</strong> {selectedAccount.device_id || 'None'}</p>
              <p><strong>Status:</strong> <span className={`status-badge ${selectedAccount.status}`}>{selectedAccount.status}</span></p>

              <div className="assignment-control">
                <h4>Soul Binding</h4>
                {selectedAccount.agent_id ? (
                  <div className="assigned-view">
                    <p>Bound to soul <strong>{selectedAccount.agent_id}</strong></p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button onClick={() => handleUnbind(selectedAccount.id)} className="btn-danger">
                        <Unlink size={16} /> Unbind Soul
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
                      <option value="">Select a floating soul…</option>
                      {profiles.map(p => (
                        <option key={p.agent_id} value={p.agent_id}>
                          {p.full_name || p.codename} ({p.agent_id}) · {p.status}
                        </option>
                      ))}
                    </select>
                    <button onClick={() => handleBind(selectedAccount.id, bindAgentId)} className="btn-primary" disabled={!bindAgentId}>
                      <Link size={16} /> Bind to Soul
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="audit-logs">
              <h3><History size={18}/> Audit History</h3>
              {logs.length === 0 ? <p className="text-muted">No history found.</p> : (
                <ul>
                  {logs.map(log => (
                    <li key={log.id}>
                      <span className="time">{new Date(log.timestamp).toLocaleString()}</span>
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
