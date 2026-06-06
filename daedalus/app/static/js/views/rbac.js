/** DAEDALUS — RBAC View */
const RBACView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Access Control</h1><p class="view-subtitle">Roles & permissions management</p></div>
                <button class="btn btn-primary btn-sm" id="btn-add-role">+ New Role</button>
            </div>
            <div id="rbac-content"><div class="loading" style="padding:40px;text-align:center;color:var(--text-muted)">Loading...</div></div>
        `;
        document.getElementById('btn-add-role').onclick = () => this.showCreateRoleModal();
        await this.loadData();
    },

    async loadData() {
        try {
            const rolesData = await API.get('/api/v1/roles');
            const permsData = await API.get('/api/v1/permissions');
            const roles = rolesData.roles || rolesData;
            const perms = permsData.permissions || permsData;
            this.renderRoles(roles, perms);
        } catch (err) { showToast('Failed to load RBAC: ' + err.message, 'error'); }
    },

    renderRoles(roles, allPerms) {
        if (!roles.length) {
            document.getElementById('rbac-content').innerHTML = '<div class="empty-state"><div class="empty-state-icon">⬡</div>No roles configured</div>';
            return;
        }
        document.getElementById('rbac-content').innerHTML = roles.map(role => {
            const rolePerms = new Set((role.permissions || []).map(p => typeof p === 'string' ? p : p.permission));
            return `
            <div class="card" style="margin-bottom:16px;">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
                    <div>
                        <span style="font-weight:600;font-size:1rem;">${role.name}</span>
                        <span style="color:var(--text-muted);font-size:0.8rem;margin-left:8px;">${role.description || ''}</span>
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="RBACView.deleteRole(${role.id})">Delete</button>
                </div>
                <div class="perm-grid">${allPerms.map(p => `
                    <label class="perm-item">
                        <input type="checkbox" ${rolePerms.has(p) ? 'checked' : ''} onchange="RBACView.togglePerm(${role.id}, '${p}', this.checked)">
                        <span>${p}</span>
                    </label>
                `).join('')}</div>
            </div>`;
        }).join('');
    },

    async togglePerm(roleId, perm, enabled) {
        try {
            if (enabled) {
                await API.post(`/api/v1/roles/${roleId}/permissions`, { permission: perm });
            } else {
                await API.del(`/api/v1/roles/${roleId}/permissions/${encodeURIComponent(perm)}`);
            }
            showToast(`Permission ${enabled ? 'granted' : 'revoked'}`, 'success');
        } catch (err) {
            showToast(err.message, 'error');
            this.loadData();
        }
    },

    showCreateRoleModal() {
        openModal(`
            <div class="modal-header"><h2 class="modal-title">Create Role</h2><button class="modal-close" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
                <div class="form-group"><label>Role Name</label><input id="new-role-name" placeholder="Operator"></div>
                <div class="form-group"><label>Description</label><input id="new-role-desc" placeholder="Description"></div>
            </div>
            <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="RBACView.submitCreateRole()">Create</button></div>
        `);
    },

    async submitCreateRole() {
        try {
            const data = {
                name: document.getElementById('new-role-name').value,
                description: document.getElementById('new-role-desc').value,
            };
            await API.post('/api/v1/roles', data);
            closeModal(); showToast('Role created', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    },

    async deleteRole(roleId) {
        if (!confirm('Delete this role?')) return;
        try {
            await API.del(`/api/v1/roles/${roleId}`);
            showToast('Role deleted', 'success'); this.loadData();
        } catch (err) { showToast(err.message, 'error'); }
    }
};
