// Connecting Spotify (developers).
import { html, raw } from '../core/html.js';
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';

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
        badge = `<span class="u-c-danger">⚠ Erreur</span>`;
        body = String(html`<div class="u-c-text-muted u-fs-0_85rem">Impossible de lire l'état : ${s.error}</div>`);
    } else if (!s.configured) {
        badge = `<span class="u-c-warning">○ Non configuré</span>`;
        body = `<div class="u-c-text-muted u-fs-0_85rem">Renseigne Client ID et Client Secret ci-dessus, puis recharge la page.</div>`;
    } else if (!s.authorized) {
        badge = `<span class="u-c-warning">○ Non autorisé</span>`;
        body = `<div class="u-c-text-muted u-fs-0_85rem">Aucun token en cache. Clique sur « Se connecter » pour démarrer l'autorisation OAuth.</div>`;
    } else {
        const acct = s.account || {};
        const exp = s.expires_at ? new Date(s.expires_at * 1000) : null;
        const stale = s.is_expired;
        badge = stale
            ? `<span class="u-c-warning">⏳ Expiré (refresh à la prochaine requête)</span>`
            : `<span class="u-c-success">● Connecté</span>`;
        body = String(html`
                <div class="u-fs-0_9rem u-lh-1_6">
                    <div><strong>Compte :</strong> ${acct.display_name || '?'} <code>${acct.id || '?'}</code></div>
                    ${raw(exp ? `<div><strong>Token valide jusqu'à :</strong> ${exp.toLocaleString()}</div>` : '')}
                    ${raw(s.scope ? String(html`<div class="u-c-text-muted u-fs-0_8rem"><strong>Scopes :</strong> ${s.scope}</div>`) : '')}
                </div>
            `);
    }

    const btnLabel = s.authorized ? '🔄 Re-authoriser' : '🔑 Se connecter';
    const btnClass = s.authorized ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm';
    const cbUrl = s.callback_url || '';
    const cbHint = cbUrl ? String(html`
            <div class="u-mt-8 u-p-8 u-bg-bg-primary u-br-6 u-fs-0_8rem u-c-text-muted">
                Redirect URI à enregistrer dans le dashboard Spotify :
                <code class="u-us-all">${cbUrl}</code>
            </div>`) : '';

    return `
            <div class="u-mt-12 u-p-12 u-bg-bg-secondary u-br-8 u-bd-1-solid-border">
                <div class="u-d-flex u-jc-space-between u-ai-center u-mb-8">
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
