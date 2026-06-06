/** DAEDALUS — Souls / Agent Profiles View */
const SoulsView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Agent Souls</h1><p class="view-subtitle">Psychological profiles & mission control</p></div>
                <button class="btn btn-primary btn-sm" id="btn-add-soul">+ New Agent</button>
            </div>
            <div class="toolbar"><select id="filter-caste" style="width:160px;"><option value="">All Castes</option><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="gamma">Gamma</option></select></div>
            <div class="profile-grid" id="souls-grid"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading profiles...</div></div>
        `;
        document.getElementById('btn-add-soul').onclick = () => this.showCreateModal();
        document.getElementById('filter-caste').onchange = () => this.loadData();
        await this.loadData();
    },

    async loadData() {
        try {
            const caste = document.getElementById('filter-caste').value;
            let url = '/api/v1/souls/profiles';
            if (caste) url += `?caste=${caste}`;
            const profiles = await API.get(url);
            this.renderGrid(profiles);
        } catch (err) { showToast('Failed to load profiles: ' + err.message, 'error'); }
    },

    renderGrid(profiles) {
        if (!profiles.length) {
            document.getElementById('souls-grid').innerHTML = '<div class="empty-state"><div class="empty-state-icon">◇</div>No agent profiles</div>';
            return;
        }
        document.getElementById('souls-grid').innerHTML = profiles.map(p => `
            <div class="profile-card" onclick='SoulsView.showDetail(${JSON.stringify(p).replace(/'/g,"&#39;")})'>
                <div class="profile-card-header">
                    <div>
                        <div class="profile-name">${p.full_name}</div>
                        <div class="profile-agent-id">${p.agent_id} / ${p.codename}</div>
                    </div>
                    <span class="badge badge-${p.caste}">${p.caste}</span>
                </div>
                <div class="profile-detail">📍 ${p.residence_city || '—'}, ${p.residence_state || '—'}</div>
                <div class="profile-detail">💼 ${p.profession || '—'}</div>
                <div class="profile-detail">⏰ Active ${p.active_hours_start}:00 – ${p.active_hours_end}:00</div>
                ${p.core_mission ? `<div class="profile-detail" style="color:var(--accent-1);font-size:0.8rem;margin-top:8px;">🎯 ${p.core_mission.substring(0, 80)}${p.core_mission.length > 80 ? '...' : ''}</div>` : ''}
                <div class="profile-platforms">${(p.platforms || []).map(pl => `<span class="tag">${pl}</span>`).join('')}</div>
            </div>
        `).join('');
    },

    showDetail(profile) {
        const p = typeof profile === 'string' ? JSON.parse(profile) : profile;
        const cs = p.communication_style || {};
        const br = p.behavioral_rules || {};
        openModal(`
            <div class="modal-header"><h2 class="modal-title">${p.full_name} <span style="color:var(--text-muted);font-size:0.85rem;">(${p.agent_id})</span></h2><button class="modal-close" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
                <div class="detail-panel">
                    <div class="form-group"><label>Codename</label><input id="ed-codename" value="${p.codename}"></div>
                    <div class="form-group"><label>Caste</label><select id="ed-caste"><option value="alpha" ${p.caste==='alpha'?'selected':''}>Alpha</option><option value="beta" ${p.caste==='beta'?'selected':''}>Beta</option><option value="gamma" ${p.caste==='gamma'?'selected':''}>Gamma</option></select></div>
                    <div class="form-group"><label>Full Name</label><input id="ed-fullname" value="${p.full_name}"></div>
                    <div class="form-group"><label>Profession</label><input id="ed-profession" value="${p.profession || ''}"></div>
                    <div class="form-group"><label>City</label><input id="ed-city" value="${p.residence_city || ''}"></div>
                    <div class="form-group"><label>State</label><input id="ed-state" value="${p.residence_state || ''}"></div>
                    <div class="form-group"><label>Active Hours Start</label><input type="number" id="ed-hours-start" value="${p.active_hours_start}" min="0" max="23"></div>
                    <div class="form-group"><label>Active Hours End</label><input type="number" id="ed-hours-end" value="${p.active_hours_end}" min="0" max="23"></div>
                    <div class="form-group full-width"><label>Core Mission</label><textarea id="ed-mission" style="min-height:60px;font-family:var(--font-sans);">${p.core_mission || ''}</textarea></div>
                    <div class="form-group full-width"><label>Communication Style (JSON)</label><textarea id="ed-comstyle">${JSON.stringify(cs, null, 2)}</textarea></div>
                    <div class="form-group full-width"><label>Behavioral Rules (JSON)</label><textarea id="ed-rules">${JSON.stringify(br, null, 2)}</textarea></div>
                    <div class="form-group full-width"><label>Platforms (comma-separated)</label><input id="ed-platforms" value="${(p.platforms || []).join(', ')}"></div>
                    <div class="form-group full-width"><label>Stance Modifiers (JSON)</label><textarea id="ed-stance">${JSON.stringify(p.current_stance_modifiers || {}, null, 2)}</textarea></div>
                </div>
            </div>
            <div class="modal-actions">
                <button class="btn btn-danger btn-sm" onclick="SoulsView.deleteProfile('${p.agent_id}')">Delete</button>
                <div style="flex:1"></div>
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="SoulsView.saveProfile('${p.agent_id}')">Save Changes</button>
            </div>
        `);
    },

    async saveProfile(agentId) {
        try {
            const parseJSON = (id) => { try { return JSON.parse(document.getElementById(id).value); } catch { return {}; } };
            const data = {
                codename: document.getElementById('ed-codename').value,
                caste: document.getElementById('ed-caste').value,
                full_name: document.getElementById('ed-fullname').value,
                profession: document.getElementById('ed-profession').value,
                residence_city: document.getElementById('ed-city').value,
                residence_state: document.getElementById('ed-state').value,
                active_hours_start: parseInt(document.getElementById('ed-hours-start').value),
                active_hours_end: parseInt(document.getElementById('ed-hours-end').value),
                core_mission: document.getElementById('ed-mission').value || null,
                communication_style: parseJSON('ed-comstyle'),
                behavioral_rules: parseJSON('ed-rules'),
                platforms: document.getElementById('ed-platforms').value.split(',').map(s => s.trim()).filter(Boolean),
                current_stance_modifiers: parseJSON('ed-stance'),
            };
            await API.put(`/api/v1/souls/profiles/${agentId}`, data);
            closeModal(); showToast('Profile updated', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    },

    async deleteProfile(agentId) {
        if (!confirm(`Delete profile for agent ${agentId}?`)) return;
        try {
            await API.del(`/api/v1/souls/profiles/${agentId}`);
            closeModal(); showToast('Profile deleted', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    },

    showCreateModal() {
        openModal(`
            <div class="modal-header"><h2 class="modal-title">Create Agent Profile</h2><button class="modal-close" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
                <div class="detail-panel">
                    <div class="form-group"><label>Agent ID</label><input id="new-agentid" placeholder="003"></div>
                    <div class="form-group"><label>Codename</label><input id="new-codename" placeholder="agent_name"></div>
                    <div class="form-group"><label>Full Name</label><input id="new-fullname" placeholder="Имя Фамилия"></div>
                    <div class="form-group"><label>Caste</label><select id="new-caste"><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="gamma">Gamma</option></select></div>
                    <div class="form-group"><label>Profession</label><input id="new-profession"></div>
                    <div class="form-group"><label>City</label><input id="new-city"></div>
                    <div class="form-group full-width"><label>Platforms (comma-separated)</label><input id="new-platforms" value="telegram, instagram"></div>
                </div>
            </div>
            <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="SoulsView.submitCreate()">Create</button></div>
        `);
    },

    async submitCreate() {
        try {
            const data = {
                agent_id: document.getElementById('new-agentid').value,
                codename: document.getElementById('new-codename').value,
                full_name: document.getElementById('new-fullname').value,
                caste: document.getElementById('new-caste').value,
                profession: document.getElementById('new-profession').value,
                residence_city: document.getElementById('new-city').value,
                platforms: document.getElementById('new-platforms').value.split(',').map(s => s.trim()).filter(Boolean),
            };
            await API.post('/api/v1/souls/profiles', data);
            closeModal(); showToast('Agent profile created', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    }
};
