// Connecting Spotify (developers).
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';

export async function loadSpotifyAuth() {
    try {
        state.spotifyAuth = await api('/api/spotify/status');
    } catch (e) {
        state.spotifyAuth = { error: String(e?.message || e) };
    }
}

export function renderSpotifyAuthPanel() {
    const s = state.spotifyAuth || {};
    let badge, body;
    if (s.error) {
        badge = `<span style="color:var(--danger)">⚠ Erreur</span>`;
        body = `<div style="color:var(--text-muted);font-size:0.85rem">Impossible de lire l'état : ${escapeHtml(s.error)}</div>`;
    } else if (!s.configured) {
        badge = `<span style="color:var(--warning)">○ Non configuré</span>`;
        body = `<div style="color:var(--text-muted);font-size:0.85rem">Renseigne Client ID et Client Secret ci-dessus, puis recharge la page.</div>`;
    } else if (!s.authorized) {
        badge = `<span style="color:var(--warning)">○ Non autorisé</span>`;
        body = `<div style="color:var(--text-muted);font-size:0.85rem">Aucun token en cache. Clique sur « Se connecter » pour démarrer l'autorisation OAuth.</div>`;
    } else {
        const acct = s.account || {};
        const exp = s.expires_at ? new Date(s.expires_at * 1000) : null;
        const stale = s.is_expired;
        badge = stale
            ? `<span style="color:var(--warning)">⏳ Expiré (refresh à la prochaine requête)</span>`
            : `<span style="color:var(--success)">● Connecté</span>`;
        body = `
                <div style="font-size:0.9rem;line-height:1.6">
                    <div><strong>Compte :</strong> ${escapeHtml(acct.display_name || '?')} <code>${escapeHtml(acct.id || '?')}</code></div>
                    ${exp ? `<div><strong>Token valide jusqu'à :</strong> ${exp.toLocaleString()}</div>` : ''}
                    ${s.scope ? `<div style="color:var(--text-muted);font-size:0.8rem"><strong>Scopes :</strong> ${escapeHtml(s.scope)}</div>` : ''}
                </div>
            `;
    }

    const btnLabel = s.authorized ? '🔄 Re-authoriser' : '🔑 Se connecter';
    const btnClass = s.authorized ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm';
    const cbUrl = s.callback_url || '';
    const cbHint = cbUrl ? `
            <div style="margin-top:8px;padding:8px;background:var(--bg-primary);border-radius:6px;font-size:0.8rem;color:var(--text-muted)">
                Redirect URI à enregistrer dans le dashboard Spotify :
                <code style="user-select:all">${escapeHtml(cbUrl)}</code>
            </div>` : '';

    return `
            <div style="margin-top:12px;padding:12px;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <div><strong>OAuth Spotify</strong> &nbsp; ${badge}</div>
                    <button class="${btnClass}" data-click="startSpotifyAuth">${btnLabel}</button>
                </div>
                ${body}
                ${cbHint}
            </div>
        `;
}

export function startSpotifyAuth() {
    window.location.href = '/spotify/auth/start';
}

registerActions({
    startSpotifyAuth,
});
