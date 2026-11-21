// The server page's overview (C2).
import { actionArgs } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { auditEntryHtml } from './audit.js';
import { refreshModuleBrowser } from './server.js';

// ── Overview (C2) ─────────────────────────────────
export function overviewHtml(sid) {
    const data = state.overview?.[sid];
    const health = state.healthProblems?.[sid];
    const activity = data?.activity;
    const tile = (label, value, note = '') => html`
            <div class="stat-card"><div class="stat-label">${label}</div>
                <div class="stat-value">${value}</div>${note && html`<div class="stat-note">${note}</div>`}</div>`;
    const xpOff = !(state.servers?.[sid]?.config?.moduleXp?.enabled);
    const activityTiles = activity
        ? html`${tile('Membres actifs · 7 j', String(activity.activeMembers))}${tile('Messages · 7 j', activity.messages.toLocaleString('fr-FR'))}`
        : tile('Activité · 7 j', '—', xpOff
            ? html`Active le <a href="${routeHash('module', sid, 'moduleXp')}" data-click="navigate" data-click-args="${actionArgs('module', sid, 'moduleXp')}" data-prevent>Système d'XP</a> pour la voir.`
            : data ? 'Indisponible pour le moment.' : 'Chargement…');
    const healthTile = health === undefined
        ? tile('Santé', '…')
        : health === null
            ? tile('Santé', '—', 'Diagnostic indisponible.')
            : tile('Santé', health === 0 ? '✅ Tout va bien' : `⚠️ ${health} problème${health > 1 ? 's' : ''}`,
                health > 0 && html`<a href="#health-card" data-click="scrollToSection" data-click-args="${actionArgs('health-card')}" data-prevent>Voir le détail</a>`);
    const recent = data?.recent;
    const recentHtml = !recent ? null : recent.length === 0
        ? html`<p class="overview-recent-empty">Aucune modification depuis le dashboard pour l'instant.</p>`
        : html`${recent.map(auditEntryHtml)}<a href="#audit-card" class="overview-more" data-click="scrollToSection" data-click-args="${actionArgs('audit-card')}" data-prevent>Tout le journal</a>`;
    return html`<div class="stats-grid overview-tiles">${activityTiles}${healthTile}</div>
            ${recentHtml && html`<h4 class="overview-subtitle">Dernières modifications</h4>${recentHtml}`}`;
}

export function refreshOverview(sid) {
    const el = document.getElementById('overview-body');
    if (el && state.currentServerId === sid) el.innerHTML = overviewHtml(sid);
}

export async function loadOverview(sid) {
    try {
        const data = await api(`/api/servers/${encodeURIComponent(sid)}/overview`);
        state.overview = { ...(state.overview || {}), [sid]: data };
    } catch (e) {
        state.overview = { ...(state.overview || {}), [sid]: { activity: null, recent: null, lastChanges: null } };
    }
    refreshOverview(sid);
    refreshModuleBrowser(sid);
}
