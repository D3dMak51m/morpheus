/** DAEDALUS — Database Explorer View */
const ExplorerView = {
    async render(container) {
        container.innerHTML = `
            <div class="view-header">
                <div><h1 class="view-title">Database Explorer</h1><p class="view-subtitle">Browse tables & execute queries</p></div>
            </div>
            <div class="toolbar">
                <select id="explorer-table" style="width:220px;"><option value="">Select table...</option></select>
                <button class="btn btn-secondary btn-sm" onclick="ExplorerView.loadTable()">Browse</button>
                <div class="toolbar-spacer"></div>
                <button class="btn btn-secondary btn-sm" onclick="ExplorerView.showSQLPanel()">SQL Console</button>
            </div>
            <div id="explorer-content"></div>
            <div id="sql-panel" style="display:none;margin-top:20px;">
                <div class="form-group"><label>Raw SQL Query</label><textarea id="sql-input" class="sql-editor" placeholder="SELECT * FROM agent_profiles LIMIT 10;"></textarea></div>
                <div style="display:flex;gap:10px;margin-top:10px;"><button class="btn btn-primary btn-sm" onclick="ExplorerView.executeSQL()">▶ Execute</button><button class="btn btn-secondary btn-sm" onclick="document.getElementById('sql-panel').style.display='none';">Close</button></div>
                <div id="sql-results" style="margin-top:14px;"></div>
            </div>
        `;
        await this.loadTables();
    },

    async loadTables() {
        try {
            const data = await API.get('/api/v1/db/tables');
            const sel = document.getElementById('explorer-table');
            (data.tables || []).forEach(t => {
                const opt = document.createElement('option');
                opt.value = t; opt.textContent = t;
                sel.appendChild(opt);
            });
        } catch (err) { showToast('Failed to list tables: ' + err.message, 'error'); }
    },

    async loadTable() {
        const table = document.getElementById('explorer-table').value;
        if (!table) return;
        try {
            const data = await API.get(`/api/v1/db/tables/${table}?limit=50`);
            this.renderTableData(data, table);
        } catch (err) { showToast(err.message, 'error'); }
    },

    renderTableData(data, tableName) {
        const rows = data.rows || [];
        const cols = data.columns || (rows.length ? Object.keys(rows[0]) : []);
        if (!rows.length) {
            document.getElementById('explorer-content').innerHTML = `<div class="empty-state"><div class="empty-state-icon">⊞</div>Table "${tableName}" is empty</div>`;
            return;
        }
        document.getElementById('explorer-content').innerHTML = `
            <p style="color:var(--text-muted);font-size:0.8rem;margin-bottom:10px;">${tableName}: ${data.total || rows.length} rows</p>
            <div class="table-wrapper"><table>
                <thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
                <tbody>${rows.map(row => `<tr>${cols.map(c => {
                    let val = row[c];
                    if (val === null) val = '<span style="color:var(--text-muted)">null</span>';
                    else if (typeof val === 'object') val = `<code style="font-size:0.75rem;color:var(--accent-1);">${JSON.stringify(val).substring(0, 80)}</code>`;
                    else val = String(val).substring(0, 100);
                    return `<td>${val}</td>`;
                }).join('')}</tr>`).join('')}</tbody>
            </table></div>`;
    },

    showSQLPanel() {
        document.getElementById('sql-panel').style.display = 'block';
        document.getElementById('sql-input').focus();
    },

    async executeSQL() {
        const sql = document.getElementById('sql-input').value.trim();
        if (!sql) return;
        try {
            const data = await API.post('/api/v1/db/query', { query: sql });
            if (data.rows) {
                this.renderTableData(data, 'Query Result');
                document.getElementById('sql-results').innerHTML = `<p style="color:var(--success);font-size:0.8rem;">${data.rows.length} rows returned</p>`;
            } else {
                document.getElementById('sql-results').innerHTML = `<p style="color:var(--success);font-size:0.8rem;">Query executed: ${JSON.stringify(data)}</p>`;
            }
        } catch (err) {
            document.getElementById('sql-results').innerHTML = `<p style="color:var(--error);font-size:0.8rem;">Error: ${err.message}</p>`;
        }
    }
};
