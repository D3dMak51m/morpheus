import { useState, useEffect } from 'react';
import { Users, History, Link, Unlink } from 'lucide-react';
import './AccountsManager.css';

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

export default function AccountsManager({ token }: { token: string | null }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [newAgentId, setNewAgentId] = useState('');

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
  }, [token]);

  useEffect(() => {
    if (selectedAccount) {
      fetchLogs(selectedAccount.id);
    }
  }, [selectedAccount]);

  const handleAssign = async (accountId: number, agentId: string | null) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/assign?agent_id=${agentId || ''}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        fetchAccounts();
        if (selectedAccount?.id === accountId) fetchLogs(accountId);
      }
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
              
              <div className="assignment-control">
                <h4>Agent Assignment</h4>
                {selectedAccount.agent_id ? (
                  <div className="assigned-view">
                    <p>Currently assigned to <strong>{selectedAccount.agent_id}</strong></p>
                    <button onClick={() => handleAssign(selectedAccount.id, null)} className="btn-danger">
                      <Unlink size={16} /> Detach Profile
                    </button>
                  </div>
                ) : (
                  <div className="assign-form">
                    <input 
                      type="text" 
                      placeholder="Agent ID..." 
                      value={newAgentId}
                      onChange={e => setNewAgentId(e.target.value)}
                    />
                    <button onClick={() => handleAssign(selectedAccount.id, newAgentId)} className="btn-primary">
                      <Link size={16} /> Assign to Agent
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
    </div>
  );
}
