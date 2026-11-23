// Connecting the bot's Twitch account (developers, P29): EventSub and
// Streamlabs' live status need a user token, which a container can't get by
// opening a browser of its own.
import { html } from '../core/html.js';
import { registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';

export async function loadTwitchAuth() {
    try {
        state.twitchAuth = await api('/api/twitch/status');
    } catch (e) {
        state.twitchAuth = { error: String(e?.message || e) };
    }
}

function twitchBadgeAndBody(s) {
    if (s.error) {
        return [html`<span class="u-c-danger">⚠ Erreur</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Impossible de lire l'état : ${s.error}</div>`];
    }
    if (!s.app_configured) {
        return [html`<span class="u-c-warning">○ Non configuré</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Renseigne Client ID et Client Secret ci-dessus, puis recharge la page.</div>`];
    }
    if (!s.connected) {
        return [html`<span class="u-c-warning">○ Non connecté</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Sans compte connecté, les notifications de live et l'état des lives Streamlabs sont arrêtés. Clique sur « Connecter Twitch ».</div>`];
    }
    if (s.unreachable) {
        return [html`<span class="u-c-warning">● Connecté (Twitch injoignable)</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Un jeton est enregistré, mais Twitch ne répond pas pour le vérifier.</div>`];
    }
    if (s.stale) {
        return [html`<span class="u-c-warning">⏳ Jeton expiré</span>`,
            html`<div class="u-c-text-muted u-fs-0_85rem">Il sera renouvelé au prochain démarrage de l'extension ; s'il a été révoqué, reconnecte le compte.</div>`];
    }
    return [html`<span class="u-c-success">● Connecté</span>`,
        html`<div class="u-fs-0_9rem u-lh-1_6">
                <div><strong>Compte :</strong> ${s.login || '?'}</div>
                ${(s.scopes || []).length > 0 && html`<div class="u-c-text-muted u-fs-0_8rem"><strong>Scopes :</strong> ${s.scopes.join(' ')}</div>`}
            </div>`];
}

export function renderTwitchAuthPanel() {
    const s = state.twitchAuth || {};
    const [badge, body] = twitchBadgeAndBody(s);
    const connected = s.connected && !s.error;
    return html`
            <div class="u-mt-12 u-p-12 u-bg-bg-secondary u-br-8 u-bd-1-solid-border">
                <div class="u-d-flex u-jc-space-between u-ai-center u-mb-8">
                    <div><strong>Compte Twitch du bot</strong> &nbsp; ${badge}</div>
                    <button class="${connected ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm'}"
                            data-click="startTwitchAuth">${connected ? '🔄 Reconnecter' : '🔌 Connecter Twitch'}</button>
                </div>
                ${body}
                ${s.callback_url && html`
                    <div class="u-mt-8 u-p-8 u-bg-bg-primary u-br-6 u-fs-0_8rem u-c-text-muted">
                        URL de redirection OAuth à enregistrer dans la console développeur de Twitch :
                        <code class="u-us-all">${s.callback_url}</code>
                    </div>`}
            </div>`;
}

export function startTwitchAuth() {
    window.location.href = '/twitch/auth/start';
}

registerActions({
    startTwitchAuth,
});
