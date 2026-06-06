/** DAEDALUS — Activity Stream View */
const StreamView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Activity Stream</h1><p class="view-subtitle">Dispatched actions audit trail</p></div>
                <button class="btn btn-secondary btn-sm" id="btn-refresh-stream">↻ Refresh</button>
            </div>
            <div class="toolbar">
                <input id="stream-agent" placeholder="Agent ID" style="width:120px;">
                <select id="stream-platform" style="width:140px;"><option value="">All Platforms</option><option value="telegram">Telegram</option><option value="instagram">Instagram</option><option value="web">Web</option></select>
                <button class="btn btn-secondary btn-sm" onclick="StreamView.loadData()">Filter</button>
            </div>
            <div class="table-wrapper" id="stream-table"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading...</div></div>
            <div style="display:flex;gap:10px;margin-top:14px;justify-content:center;" id="stream-pagination"></div>
        `;
        document.getElementById('btn-refresh-stream').onclick = () => this.loadData();
        this._offset = 0;
        await this.loadData();
    },

    async loadData() {
        try {
            const agent = document.getElementById('stream-agent').value;
            const platform = document.getElementById('stream-platform').value;
            let url = `/api/v1/analytics/stream?limit=30&offset=${this._offset}`;
            if (agent) url += `&agent_id=${agent}`;
            if (platform) url += `&platform=${platform}`;
            const data = await API.get(url);
            this.renderTable(data);
        } catch (err) { showToast('Failed to load stream: ' + err.message, 'error'); }
    },

    renderTable(data) {
        if (!data.logs.length) {
            document.getElementById('stream-table').innerHTML = '<div class="empty-state"><div class="empty-state-icon">▤</div>No activity recorded yet</div>';
            document.getElementById('stream-pagination').innerHTML = '';
            return;
        }
        document.getElementById('stream-table').innerHTML = `<table>
            <thead><tr><th>Time</th><th>Agent</th><th>Platform</th><th>Action</th><th>Target</th><th>Text</th><th>Status</th></tr></thead>
            <tbody>${data.logs.map(l => `<tr>
                <td style="font-size:0.8rem;color:var(--text-muted);white-space:nowrap;">${l.created_at ? new Date(l.created_at).toLocaleString() : '—'}</td>
                <td><span style="font-family:var(--font-mono);font-size:0.8rem;">${l.agent_id}</span></td>
                <td><span class="tag">${l.platform}</span></td>
                <td>${l.action_type}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;">${l.target_url}</td>
                <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;color:var(--text-secondary);">${l.text_content || '—'}</td>
                <td><span class="badge badge-${l.status === 'dispatched' ? 'active' : l.status === 'failed' ? 'inactive' : 'active'}">${l.status}</span></td>
            </tr>`).join('')}</tbody>
        </table>`;

        const totalPages = Math.ceil(data.total / 30);
        const currentPage = Math.floor(this._offset / 30) + 1;
        let pag = '';
        if (currentPage > 1) pag += `<button class="btn btn-secondary btn-sm" onclick="StreamView._offset -= 30; StreamView.loadData();">← Prev</button>`;
        pag += `<span style="padding:8px;color:var(--text-muted);font-size:0.8rem;">Page ${currentPage} / ${totalPages} (${data.total} total)</span>`;
        if (currentPage < totalPages) pag += `<button class="btn btn-secondary btn-sm" onclick="StreamView._offset += 30; StreamView.loadData();">Next →</button>`;
        document.getElementById('stream-pagination').innerHTML = pag;
    }
};
