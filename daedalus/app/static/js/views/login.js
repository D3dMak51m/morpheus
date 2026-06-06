/** DAEDALUS — Login View */
const LoginView = {
    init() {
        const form = document.getElementById('login-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            const errDiv = document.getElementById('login-error');
            const submitBtn = document.getElementById('login-submit');
            errDiv.style.display = 'none';
            submitBtn.disabled = true;
            submitBtn.querySelector('span:first-child').textContent = 'Signing in...';
            submitBtn.querySelector('.btn-loader').style.display = 'inline-block';
            try {
                await API.login(username, password);
                App.showApp();
            } catch (err) {
                errDiv.textContent = err.message;
                errDiv.style.display = 'block';
            } finally {
                submitBtn.disabled = false;
                submitBtn.querySelector('span:first-child').textContent = 'Sign In';
                submitBtn.querySelector('.btn-loader').style.display = 'none';
            }
        };
    }
};
