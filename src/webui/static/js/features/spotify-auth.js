// A server's Spotify account (P36): its admins connect it from the module page.
import { html } from '../core/html.js';
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';

function spotifyBadgeAndBody(s) {
    if (s.error) {
        return [html`<span class="u-c-danger">⚠ Erreur</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Impossible de lire l'état : ${s.error}</div>`];
    }
    if (!s.configured) {
        return [html`<span class="u-c-warning">○ Application manquante</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Renseigne le Client ID et le Client Secret de l'application Spotify dans les réglages, enregistre, puis connecte le compte.</div>`];
    }
    if (!s.authorized) {
        return [html`<span class="u-c-warning">○ Non connecté</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Sans compte connecté, la playlist, les votes et <code>/musique</code> sont en pause sur ce serveur.</div>`];
    }
    const acct = s.account || {};
    const exp = s.expires_at ? new Date(s.expires_at * 1000) : null;
    return [s.is_expired
        ? html`<span class="u-c-warning">⏳ Jeton expiré (renouvelé au prochain appel)</span>`
        : html`<span class="u-c-success">● Connecté</span>`,
    html`<div class="u-fs-0_9rem u-lh-1_6">
            <div><strong>Compte :</strong> ${acct.display_name || '?'} <code>${acct.id || '?'}</code></div>
            ${exp && html`<div><strong>Jeton valide jusqu'à :</strong> ${exp.toLocaleString('fr-FR')}</div>`}
        </div>`];
}

export function spotifyAuthHtml(serverId, s) {
    const [badge, body] = spotifyBadgeAndBody(s);
    const connected = s.authorized && !s.error;
    return html`
            <div class="u-p-12">
                <div class="u-d-flex u-jc-space-between u-ai-center u-mb-8">
                    <div>${badge}</div>
                    ${s.configured && html`<button class="${connected ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm'}"
                            data-click="startSpotifyAuth" data-click-args="${actionArgs(serverId)}">${connected ? '🔄 Reconnecter' : '🔌 Connecter Spotify'}</button>`}
                </div>
                ${body}
                ${s.callback_url && html`
                    <div class="u-mt-8 u-p-8 u-bg-bg-primary u-br-6 u-fs-0_8rem u-c-text-muted">
                        URL de redirection à enregistrer dans l'application Spotify :
                        <code class="u-us-all">${s.callback_url}</code>
                    </div>`}
            </div>`;
}

export async function loadSpotifyAuth(serverId) {
    const container = document.getElementById('spotify-auth');
    if (!container) return;
    let status;
    try {
        status = await api(`/api/servers/${encodeURIComponent(serverId)}/spotify/status`);
    } catch (e) {
        status = { error: String(e?.message || e) };
    }
    container.innerHTML = spotifyAuthHtml(serverId, status || {});
}

export function startSpotifyAuth(serverId) {
    window.location.href = `/spotify/auth/start?guild=${encodeURIComponent(serverId)}`;
}

registerActions({
    startSpotifyAuth,
});
