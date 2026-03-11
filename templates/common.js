// ── SUPABASE CONFIG ─────────────────────────────────────
const SUPABASE_URL = 'https://dplxjjzsrsfewiwejogn.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRwbHhqanpzcnNmZXdpd2Vqb2duIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMxNjE0MzUsImV4cCI6MjA4ODczNzQzNX0.gfu8W7P-6FVE-95kJ7kXU4D5YAT_fTplTjzsGw_3ues';
const sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

// ── AUTH GUARD ───────────────────────────────────────────
// Call at top of every protected page. Redirects to index.html if not logged in.
async function requireAuth() {
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { window.location.href = 'index.html'; return null; }
  return session.user;
}

// ── DISPLAY NAME ─────────────────────────────────────────
function getDisplayName(user) {
  return user.user_metadata?.display_name || user.email.split('@')[0];
}

// ── TOPBAR ───────────────────────────────────────────────
// Expects elements: #topbar-name, #btn-settings, #btn-logout, .topbar-brand
function initTopbar(user) {
  const nameEl = document.getElementById('topbar-name');
  if (nameEl) nameEl.textContent = getDisplayName(user);

  const brand = document.querySelector('.topbar-brand');
  if (brand) brand.onclick = () => window.location.href = 'dashboard.html';

  const btnSettings = document.getElementById('btn-settings');
  if (btnSettings) btnSettings.onclick = () => window.location.href = 'settings.html';

  const btnLogout = document.getElementById('btn-logout');
  if (btnLogout) btnLogout.onclick = async () => {
    await sb.auth.signOut();
    window.location.href = 'index.html';
  };
}

// ── LOADING OVERLAY ──────────────────────────────────────
function showLoading(msg = 'Loading...') {
  let el = document.getElementById('loading-overlay');
  if (!el) {
    el = document.createElement('div');
    el.id = 'loading-overlay';
    el.className = 'loading-overlay';
    el.innerHTML = `<div class="spinner"></div><p class="loading-text">${msg}</p>`;
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

// ── SESSION STORAGE HELPERS ──────────────────────────────
const Store = {
  set: (key, val) => sessionStorage.setItem('ct_' + key, JSON.stringify(val)),
  get: (key) => { try { return JSON.parse(sessionStorage.getItem('ct_' + key)); } catch { return null; } },
  del: (key) => sessionStorage.removeItem('ct_' + key),
};