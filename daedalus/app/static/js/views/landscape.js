/** DAEDALUS — Landscape View */
const LandscapeView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Scraping Landscape</h1><p class="view-subtitle">Manage monitoring targets</p></div>
                <button class="btn btn-primary btn-sm" id="btn-add-target">+ Add Target</button>
            </div>
            <div class="toolbar">
                <select id="filter-platform" style="width:160px;"><option value="">All Platforms</option><option value="telegram">Telegram</option><option value="web">Web</option><option value="instagram">Instagram</option></select>
                <select id="filter-active" style="width:140px;"><option value="">All Status</option><option value="true">Active</option><option value="false">Inactive</option></select>
            </div>
            <div class="table-wrapper" id="landscape-table"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading...</div></div>
        `;
        document.getElementById('btn-add-target').onclick = () => this.showAddModal();
        document.getElementById('filter-platform').onchange = () => this.loadData();
        document.getElementById('filter-active').onchange = () => this.loadData();
        await this.loadData();
    },

    async loadData() {
        try {
            const platform = document.getElementById('filter-platform').value;
            const active = document.getElementById('filter-active').value;
            let url = '/api/v1/landscape/?';
            if (platform) url += `platform=${platform}&`;
            if (active) url += `is_active=${active}&`;
            const targets = await API.get(url);
            this.renderTable(targets);
        } catch (err) { showToast('Failed to load targets: ' + err.message, 'error'); }
    },

    renderTable(targets) {
        if (!targets.length) {
            document.getElementById('landscape-table').innerHTML = '<div class="empty-state"><div class="empty-state-icon">◎</div>No targets configured</div>';
            return;
        }
        document.getElementById('landscape-table').innerHTML = `<table>
            <thead><tr><th>Platform</th><th>Target</th><th>Tags</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>${targets.map(t => `<tr>
                <td><span class="badge badge-${t.is_active ? 'active' : 'inactive'}">${t.platform}</span></td>
                <td style="font-family:var(--font-mono);font-size:0.85rem;">${t.target_identifier}</td>
                <td>${(t.associated_tags || []).map(tag => `<span class="tag">${tag}</span>`).join('')}</td>
                <td>
                    <label class="toggle"><input type="checkbox" ${t.is_active ? 'checked' : ''} onchange="LandscapeView.toggleActive(${t.id}, this.checked)"><span class="toggle-slider"></span></label>
                </td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="LandscapeView.showEditModal(${t.id}, '${t.platform}', '${t.target_identifier.replace(/'/g, "\\'")}', ${t.is_active}, '${(t.associated_tags||[]).join(',')}')">Edit</button>
                    <button class="btn btn-danger btn-sm" onclick="LandscapeView.deleteTarget(${t.id})">✕</button>
                </td>
            </tr>`).join('')}</tbody>
        </table>`;
    },

    showAddModal() {
        openModal(`
            <div class="modal-header"><h2 class="modal-title">Add Scraping Target</h2><button class="modal-close" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
                <div class="form-group"><label>Platform</label><select id="modal-platform"><option value="telegram">Telegram</option><option value="web">Web</option><option value="instagram">Instagram</option></select></div>
                <div class="form-group"><label>Target Identifier</label><input id="modal-target" placeholder="@channel_name or https://..."></div>
                <div class="form-group"><label>Tags (comma-separated)</label><input id="modal-tags" placeholder="news, politics"></div>
            </div>
            <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="LandscapeView.submitAdd()">Add Target</button></div>
        `);
    },

    async submitAdd() {
        try {
            const data = {
                platform: document.getElementById('modal-platform').value,
                target_identifier: document.getElementById('modal-target').value,
                associated_tags: document.getElementById('modal-tags').value.split(',').map(s => s.trim()).filter(Boolean),
            };
            await API.post('/api/v1/landscape/', data);
            closeModal(); showToast('Target added', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    },

    showEditModal(id, platform, target, active, tagsStr) {
        openModal(`
            <div class="modal-header"><h2 class="modal-title">Edit Target #${id}</h2><button class="modal-close" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
                <div class="form-group"><label>Platform</label><select id="modal-platform"><option value="telegram" ${platform==='telegram'?'selected':''}>Telegram</option><option value="web" ${platform==='web'?'selected':''}>Web</option><option value="instagram" ${platform==='instagram'?'selected':''}>Instagram</option></select></div>
                <div class="form-group"><label>Target Identifier</label><input id="modal-target" value="${target}"></div>
                <div class="form-group"><label>Tags (comma-separated)</label><input id="modal-tags" value="${tagsStr}"></div>
            </div>
            <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="LandscapeView.submitEdit(${id})">Save</button></div>
        `);
    },

    async submitEdit(id) {
        try {
            const data = {
                platform: document.getElementById('modal-platform').value,
                target_identifier: document.getElementById('modal-target').value,
                associated_tags: document.getElementById('modal-tags').value.split(',').map(s => s.trim()).filter(Boolean),
            };
            await API.put(`/api/v1/landscape/${id}`, data);
            closeModal(); showToast('Target updated', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    },

    async toggleActive(id, isActive) {
        try {
            await API.put(`/api/v1/landscape/${id}`, { is_active: isActive });
            showToast(`Target ${isActive ? 'activated' : 'deactivated'}`, 'success');
        } catch (err) { showToast(err.message, 'error'); this.loadData(); }
    },

    async deleteTarget(id) {
        if (!confirm('Delete this target?')) return;
        try {
            await API.del(`/api/v1/landscape/${id}`);
            showToast('Target deleted', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    }
};
