// The audit log card.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';

// ── Audit log (F09) ───────────────────────────────
// Who changed what from the dashboard: field names only, never a value.
export const AUDIT_ACTIONS = {
    'module.save': 'a enregistré le module',
    'secretsanta.draw': 'a lancé le tirage du Père Noël Secret',
    'gazette.publish': 'a publié la Gazette',
    'module.toggle': 'a basculé le module',
    'embeds.publish': 'a publié les embeds',
    'bot_message.republish': 'a republié le message',
    'public_dashboard.create': 'a créé le lien des pages publiques',
    'public_dashboard.rotate': 'a renouvelé le lien des pages publiques',
    'rolemenu.create': 'a créé le menu de rôles',
    'rolemenu.update': 'a modifié le menu de rôles',
    'rolemenu.delete': 'a supprimé le menu de rôles',
    'infraction.revoke': 'a révoqué le cas',
    'template.test': 's\'est envoyé un test du modèle',
    'global_config.save': 'a enregistré la section',
    'config.cleanup': 'a nettoyé la configuration',
    'extensions.reload_all': 'a rechargé toutes les extensions',
    'extensions.reload': 'a rechargé l\'extension',
    'extensions.toggle': 'a basculé l\'extension',
    'short_url.create': 'a créé le lien court',
    'short_url.update': 'a modifié le lien court',
    'short_url.delete': 'a supprimé le lien court',
    'spotify.authorize': 'a reconnecté le compte Spotify',
    'onboarding.pack': 'a appliqué le pack',
    'onboarding.dismiss': 'a changé la liste « Pour bien démarrer »',
    'onboarding.announce': 'a annoncé Michel aux membres',
};

export function renderAuditCard() {
    return html`
            <div class="card" id="audit-card">
                <div class="card-header">
                    <div class="card-title">Journal des modifications</div>
                </div>
                <p style="color:var(--text-muted); margin:0 0 12px;">
                    Ce qui a été fait depuis le dashboard, par qui et quand (un an d'historique).
                    Seuls les noms des champs modifiés sont notés, jamais leurs valeurs.
                </p>
                <div id="audit-list"><div class="loading"><div class="spinner"></div></div></div>
            </div>`;
}

export function auditDetails(details) {
    const parts = [];
    if ('enabled' in details) parts.push(details.enabled ? 'activé' : 'désactivé');
    if ('count' in details) parts.push(`${details.count} embed(s)`);
    if ('participants' in details) parts.push(`${details.participants} participant(s)`);
    if ('dismissed' in details) parts.push(details.dismissed ? 'masquée' : 'réaffichée');
    if ('reloaded' in details) parts.push(`${details.reloaded} rechargée(s), ${details.failed} en échec`);
    if (details.error) parts.push('avec une erreur');
    return parts.join(', ');
}

export function auditEntryHtml(e) {
    const when = e.at ? new Date(e.at).toLocaleString('fr-FR') : '';
    const what = AUDIT_ACTIONS[e.action] || e.action;
    const label = e.target ? (state.moduleSchemas[e.target]?.label || e.target) : '';
    const extra = auditDetails(e.details || {});
    const changes = (e.changes || []).length > 0
        && html`<div style="color:var(--text-muted); font-size:0.85em;">Champs : ${e.changes.map((c, i) => html`${i ? ', ' : ''}<code>${c}</code>`)}</div>`;
    return html`
            <div class="audit-entry" style="padding:6px 0; border-bottom:1px solid var(--border);">
                <div><span style="color:var(--text-muted)">${when}</span> ·
                    <strong>${e.username || e.user_id || '?'}</strong>
                    ${what} ${label && html`<strong>${label}</strong>`}
                    ${extra && `(${extra})`}</div>
                ${changes}
            </div>`;
}

export async function loadAudit(serverId, before) {
    const container = document.getElementById('audit-list');
    if (!container) return;
    const base = serverId ? `/api/servers/${encodeURIComponent(serverId)}/audit` : '/api/audit';
    const url = before ? `${base}?before=${encodeURIComponent(before)}` : base;
    let entries = [];
    try {
        const data = await api(url);
        entries = data?.entries || [];
    } catch (e) {
        container.innerHTML = html`<p style="color:var(--text-muted)">Journal indisponible.</p>`;
        return;
    }
    container.querySelector('.audit-more')?.remove();
    if (!before) container.innerHTML = '';
    if (!before && entries.length === 0) {
        container.innerHTML = html`<p style="color:var(--text-muted)">Aucune modification enregistrée.</p>`;
        return;
    }
    container.insertAdjacentHTML('beforeend', html`${entries.map(auditEntryHtml)}`);
    if (entries.length >= 100) {
        const last = entries[entries.length - 1].id;
        container.insertAdjacentHTML('beforeend', html`
                <button type="button" class="btn btn-ghost btn-sm audit-more" style="margin-top:8px;"
                        data-click="loadAudit" data-click-args="${actionArgs(serverId || null, last)}">Plus ancien…</button>`);
    }
}

registerActions({
    loadAudit,
});
