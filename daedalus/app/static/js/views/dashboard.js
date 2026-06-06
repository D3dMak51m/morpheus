/** DAEDALUS — Dashboard View */
const DashboardView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Dashboard</h1><p class="view-subtitle">System metrics & queue status</p></div>
                <button class="btn btn-secondary btn-sm" id="btn-refresh-dash">↻ Refresh</button>
            </div>
            <div class="cards-grid" id="metrics-grid"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading metrics...</div></div>
            <h2 style="font-size:1.1rem;font-weight:600;margin:28px 0 16px;">Queue Depths</h2>
            <div class="cards-grid" id="queues-grid"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading queues...</div></div>
        `;
        document.getElementById('btn-refresh-dash').onclick = () => this.loadData();
        await this.loadData();
    },

    async loadData() {
        try {
            const [metrics, queues] = await Promise.all([
                API.get('/api/v1/analytics/metrics'),
                API.get('/api/v1/analytics/queues')
            ]);
            this.renderMetrics(metrics);
            this.renderQueues(queues);
        } catch (err) { showToast('Failed to load dashboard: ' + err.message, 'error'); }
    },

    renderMetrics(data) {
        const m = data.metrics;
        const a = data.agents;
        const failRatio = (m.guardrail_failure_ratio * 100).toFixed(1);
        document.getElementById('metrics-grid').innerHTML = `
            <div class="card">
                <div class="card-label">Total Comments Sent</div>
                <div class="card-value accent">${m.total_comments_sent.toLocaleString()}</div>
                <div class="card-footer">Across all agents and platforms</div>
            </div>
            <div class="card">
                <div class="card-label">Guardrail Checks</div>
                <div class="card-value">${m.guardrail_checks_total.toLocaleString()}</div>
                <div class="card-footer"><span class="stat-dot ${parseFloat(failRatio) > 5 ? 'red' : 'green'}"></span>Failure rate: ${failRatio}%</div>
            </div>
            <div class="card">
                <div class="card-label">Active Agents</div>
                <div class="card-value accent">${a.active}</div>
                <div class="card-footer"><span class="stat-dot green"></span>${a.total} total, ${a.banned_or_disabled} banned</div>
            </div>
            <div class="card">
                <div class="card-label">System Status</div>
                <div class="card-value" style="font-size:1.4rem;color:var(--success)">● OPERATIONAL</div>
                <div class="card-footer">All subsystems connected</div>
            </div>
        `;
    },

    renderQueues(data) {
        const q = data.queues;
        const maxDepth = Math.max(...Object.values(q), 1);
        document.getElementById('queues-grid').innerHTML = Object.entries(q).map(([name, depth]) => `
            <div class="card">
                <div class="card-label">${name.replace('queue:', '')}</div>
                <div class="card-value">${depth}</div>
                <div class="queue-bar"><div class="queue-bar-fill" style="width:${Math.min(100, (depth / maxDepth) * 100)}%"></div></div>
                <div class="card-footer">${depth === 0 ? 'Empty' : depth + ' pending tasks'}</div>
            </div>
        `).join('');
    }
};
