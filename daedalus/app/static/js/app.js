/** DAEDALUS — SPA Main Application & Router */
const App = {
    views: {
        dashboard: DashboardView,
        landscape: LandscapeView,
        souls: SoulsView,
        stream: StreamView,
        rbac: RBACView,
        explorer: ExplorerView,
    },
    currentView: null,

    init() {
        LoginView.init();

        if (API.isLoggedIn()) {
            this.showApp();
        } else {
            this.showLogin();
        }

        window.addEventListener('hashchange', () => this.route());
        document.getElementById('btn-logout').onclick = () => API.logout();
    },

    showLogin() {
        document.getElementById('login-screen').style.display = 'flex';
        document.getElementById('app-shell').style.display = 'none';
    },

    showApp() {
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('app-shell').style.display = 'flex';
        document.getElementById('user-display-name').textContent = API.username;
        document.getElementById('user-badge').querySelector('.user-avatar').textContent = API.username.charAt(0).toUpperCase();

        if (!window.location.hash || window.location.hash === '#/login') {
            window.location.hash = '#/dashboard';
        }
        this.route();
    },

    async route() {
        const hash = window.location.hash.replace('#/', '') || 'dashboard';
        const viewName = hash.split('?')[0];
        const view = this.views[viewName];

        if (!view) {
            window.location.hash = '#/dashboard';
            return;
        }

        // Update sidebar active state
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.view === viewName);
        });

        const container = document.getElementById('view-container');
        container.innerHTML = '';
        container.style.animation = 'none';
        container.offsetHeight; // trigger reflow
        container.style.animation = 'fadeIn 0.3s ease';

        this.currentView = viewName;

        try {
            await view.render(container);
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠</div>Failed to load view: ${err.message}</div>`;
        }
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());
