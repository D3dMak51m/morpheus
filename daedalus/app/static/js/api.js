/**
 * DAEDALUS — API Client
 * JWT-authenticated fetch wrapper with auto-logout on 401.
 */
const API = {
    BASE: '',
    _token: null,

    get token() {
        if (!this._token) this._token = localStorage.getItem('daedalus_token');
        return this._token;
    },
    set token(val) {
        this._token = val;
        if (val) localStorage.setItem('daedalus_token', val);
        else localStorage.removeItem('daedalus_token');
    },

    get username() { return localStorage.getItem('daedalus_user') || 'operator'; },
    set username(val) {
        if (val) localStorage.setItem('daedalus_user', val);
        else localStorage.removeItem('daedalus_user');
    },

    isLoggedIn() { return !!this.token; },

    async login(username, password) {
        const body = new URLSearchParams({ username, password });
        const resp = await fetch(`${this.BASE}/api/v1/auth/login`, {
            method: 'POST', body,
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Login failed');
        }
        const data = await resp.json();
        this.token = data.access_token;
        this.username = username;
        return data;
    },

    logout() {
        this.token = null;
        this.username = null;
        window.location.hash = '#/login';
        window.location.reload();
    },

    async request(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (this.token) headers['Authorization'] = `Bearer ${this.token}`;
        if (options.json) {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(options.json);
            delete options.json;
        }
        const resp = await fetch(`${this.BASE}${url}`, { ...options, headers });
        if (resp.status === 401) {
            this.logout();
            throw new Error('Session expired');
        }
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        if (resp.status === 204) return null;
        return resp.json();
    },

    get(url) { return this.request(url); },
    post(url, json) { return this.request(url, { method: 'POST', json }); },
    put(url, json) { return this.request(url, { method: 'PUT', json }); },
    del(url) { return this.request(url, { method: 'DELETE' }); },
};

/* ── Toast Notifications ─────────────────────────────────────────── */
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateX(40px)'; setTimeout(() => toast.remove(), 300); }, 3000);
}

/* ── Modal Helpers ───────────────────────────────────────────────── */
function openModal(html) {
    const overlay = document.getElementById('modal-overlay');
    document.getElementById('modal-content').innerHTML = html;
    overlay.style.display = 'flex';
    overlay.onclick = (e) => { if (e.target === overlay) closeModal(); };
}
function closeModal() { document.getElementById('modal-overlay').style.display = 'none'; }
