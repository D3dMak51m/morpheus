import { useState } from 'react';
import SystemDiagnostics from './SystemDiagnostics';
import './Dashboard.css';

interface DashboardProps {
  token: string;
}

export default function Dashboard({ token }: DashboardProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'diagnostics'>('overview');

  return (
    <div className="view-container dashboard-container">
      <div className="dashboard-header">
        <h1>System Dashboard</h1>
        <div className="tabs dashboard-tabs">
          <button 
            className={`tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            Tab 1: Overview
          </button>
          <button 
            className={`tab-btn ${activeTab === 'diagnostics' ? 'active' : ''}`}
            onClick={() => setActiveTab('diagnostics')}
          >
            Tab 2: Diagnostics
          </button>
        </div>
      </div>

      <div className="dashboard-content">
        {activeTab === 'overview' && (
          <div className="overview-panel">
            <h2>Welcome to Daedalus — Morpheus Control Panel.</h2>
            <p className="text-muted">Select a module from the sidebar to begin.</p>
          </div>
        )}

        {activeTab === 'diagnostics' && (
          <SystemDiagnostics token={token} />
        )}
      </div>
    </div>
  );
}
