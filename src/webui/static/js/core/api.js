// Calls to the dashboard API, and what a failed one tells the user.
import { withRoleNames } from '../forms/pickers/roles.js';
import { toast } from '../ui/toast.js';
import { render } from './render.js';
import { state } from './state.js';

// ── API helpers ───────────────────────────────────
// FastAPI answers a validation error with a list of {loc, msg}: turn any
// `detail` into one readable line ("[object Object]" otherwise).
export function errorText(detail, status) {
    if (Array.isArray(detail)) {
        const parts = detail.map((d) => {
            if (!d || typeof d !== 'object') return String(d);
            const where = Array.isArray(d.loc) && d.loc.length ? `${d.loc[d.loc.length - 1]} : ` : '';
            return `${where}${d.msg || ''}`.trim();
        }).filter(Boolean);
        if (parts.length) return parts.join(' · ');
    }
    // A refusal names a role as Discord would (<@&id>): show its name.
    if (typeof detail === 'string' && detail) return withRoleNames(detail);
    if (detail && typeof detail === 'object') return JSON.stringify(detail);
    return `Erreur HTTP ${status}`;
}

export class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

export async function api(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    let resp;
    try {
        resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...options.headers },
            ...options,
        });
    } catch (e) {
        toast('Le dashboard ne répond pas. Vérifie ta connexion.', 'error');
        throw e;
    }
    if (resp.status === 401) {
        if (!state.user) {
            render();  // never logged in: the login page
            return null;
        }
        // Expired while working: keep the page (and what was typed in it),
        // say so, and fail the action rather than pretend it went through.
        showSessionExpired();
        throw new ApiError('Session expirée : reconnecte-toi pour enregistrer.', 401);
    }
    if (resp.status === 403 && method === 'GET') {
        // A developer-only read asked by someone who isn't one: nothing to show.
        return null;
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const message = errorText(err.detail, resp.status);
        toast(message, 'error');
        throw new ApiError(message, resp.status);
    }
    return await resp.json();
}

// A banner over the page, not the login screen: unsaved edits stay visible.
export function showSessionExpired() {
    if (document.getElementById('session-expired')) return;
    const banner = document.createElement('div');
    banner.id = 'session-expired';
    banner.className = 'session-expired-banner';
    banner.setAttribute('role', 'alert');
    banner.innerHTML = `Ta session a expiré. Tes modifications non enregistrées sont encore à l'écran :
            <a href="/auth/login" target="_blank" rel="noopener">reconnecte-toi dans un nouvel onglet</a>, puis réessaie.`;
    document.body.prepend(banner);
}
