// ── SUPABASE CONFIG ─────────────────────────────────────
const SUPABASE_URL = 'https://dplxjjzsrsfewiwejogn.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRwbHhqanpzcnNmZXdpd2Vqb2duIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMxNjE0MzUsImV4cCI6MjA4ODczNzQzNX0.gfu8W7P-6FVE-95kJ7kXU4D5YAT_fTplTjzsGw_3ues'; // Keeping your key

const sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

// ── AUTH GUARD ───────────────────────────────────────────
async function requireAuth() {
    const { data: { session }, error } = await sb.auth.getSession();

    const path = window.location.pathname;
    const isLoginPage = path === '/' ||
                        path === '/app/' ||
                        path.endsWith('index.html') ||
                        path.endsWith('index');

    if (!session && !isLoginPage) {
        console.log("No session found, redirecting to login...");
        window.location.href = '/app/index.html';
        return null;
    }

    if (session && isLoginPage) {
        console.log("Session found, redirecting to dashboard...");
        window.location.href = '/app/dashboard.html';
        return null;
    }

    return session ? session.user : null;
}

// ── DISPLAY NAME ─────────────────────────────────────────
function getDisplayName(user) {
    if (!user) return '';
    return user.user_metadata?.display_name || user.email?.split('@')[0] || 'User';
}

// ── TOPBAR INITIALIZATION ────────────────────────────────
function initTopbar(user) {
    if (!user) return;

    const nameEl = document.getElementById('topbar-name');
    if (nameEl) nameEl.textContent = getDisplayName(user);

    const brand = document.querySelector('.topbar-brand');
    if (brand) brand.onclick = () => window.location.href = '/app/dashboard.html';

    const btnSettings = document.getElementById('btn-settings');
    if (btnSettings) btnSettings.onclick = () => window.location.href = '/app/settings.html';

    const btnLogout = document.getElementById('btn-logout');
    if (btnLogout) {
        btnLogout.onclick = async () => {
            showLoading('Logging out...');
            await sb.auth.signOut();
            window.location.href = '/app/index.html';
        };
    }
}

// ── LOADING OVERLAY ──────────────────────────────────────
function showLoading(msg = 'Loading...') {
    let el = document.getElementById('loading-overlay');
    if (!el) {
        el = document.createElement('div');
        el.id = 'loading-overlay';
        el.className = 'loading-overlay';
        el.innerHTML = `
            <div class="spinner-container">
                <div class="spinner"></div>
                <p class="loading-text">${msg}</p>
            </div>`;
        document.body.appendChild(el);
    } else {
        el.querySelector('.loading-text').textContent = msg;
        el.classList.remove('hidden');
    }
}

function hideLoading() {
    const el = document.getElementById('loading-overlay');
    if (el) el.classList.add('hidden');
}

// ── STORAGE HELPERS ──────────────────────────────────────
const Store = {
    set: (key, val) => sessionStorage.setItem('ct_' + key, JSON.stringify(val)),
    get: (key) => {
        try {
            const item = sessionStorage.getItem('ct_' + key);
            return item ? JSON.parse(item) : null;
        } catch { return null; }
    },
    del: (key) => sessionStorage.removeItem('ct_' + key),
};