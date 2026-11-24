// The server health card.
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { refreshOverview } from './overview.js';

// ── Guild health (F08) ────────────────────────────
export function moduleLabel(mod) {
    return state.moduleSchemas[mod]?.label || mod;
}

export function fmtEpoch(seconds) {
    return seconds ? new Date(seconds * 1000).toLocaleString('fr-FR') : 'jamais';
}

export function healthLine(ok, content) {
    return html`<div class="u-mb-6 message-status ${ok ? 'configured' : 'pending'}">
            <span class="status-icon">${ok ? '✅' : '⚠️'}</span><span class="status-text">${content}</span></div>`;
}

export function healthChannelsHtml(channels) {
    if (channels === null) return html`<p class="u-c-text-muted">Bot non connecté : salons non vérifiés.</p>`;
    if (!channels.length) return html`<p class="u-c-text-muted">Aucun salon configuré.</p>`;
    return html`${channels.map(c => {
        const mods = c.modules.map(moduleLabel).join(', ');
        const name = c.name ? `#${c.name}` : html`<code>${c.channelId}</code>`;
        if (!c.found) return healthLine(false, html`${name} (${mods}) : salon introuvable — supprimé, ou le bot ne le voit pas.`);
        if (c.missing.length) return healthLine(false, html`${name} (${mods}) : il manque au bot ${c.missing.join(', ')}.`);
        return healthLine(true, html`${name} (${mods})`);
    })}`;
}

export function healthFeedsHtml(feeds) {
    if (!feeds) return html``;
    if (!feeds.length) return html`<p class="u-c-text-muted">Aucun flux RSS.</p>`;
    return html`${feeds.map(f => {
        const next = f.nextPollAt ? new Date(f.nextPollAt).toLocaleString('fr-FR') : 'au prochain cycle';
        if (f.failures) {
            return healthLine(false, html`<strong>${f.label}</strong> : ${f.failures} échec(s) de suite
                    (${f.lastError || 'erreur inconnue'}). Prochain essai : ${next}.`);
        }
        const last = f.lastPollAt ? new Date(f.lastPollAt).toLocaleString('fr-FR') : 'jamais';
        return healthLine(true, html`<strong>${f.label}</strong> : relevé le ${last}.`);
    })}`;
}

export function healthTasksHtml(tasks) {
    if (!tasks.length) return html``;
    return html`${tasks.map(t => {
        const failing = t.lastFailure && (!t.lastSuccess || t.lastFailure > t.lastSuccess);
        const when = t.schedule && html` <span class="u-c-text-muted">(${t.schedule})</span>`;
        return healthLine(!failing, html`<strong title="${t.name}">${t.label || t.name}</strong>${when} : dernier succès ${fmtEpoch(t.lastSuccess)}
                ${t.failures ? `, ${t.failures} échec(s) depuis le démarrage (dernier : ${fmtEpoch(t.lastFailure)})` : ''}`);
    })}`;
}

// The accounts the server's modules run on (P36, P37): each set on its module's page.
export function healthAccountsHtml(accounts) {
    return html`${(accounts || []).map(a => a.connected
        ? healthLine(true, html`<strong>${a.service}</strong> : compte du serveur prêt.`)
        : healthLine(false, html`<strong>${a.service}</strong> : pas de compte à ce serveur (clé ou connexion manquante), le module est en pause. Renseigne-le sur la page du module.`))}`;
}

// What the overview's "Santé" tile counts: each channel, feed, task or account in trouble.
export function healthProblemCount(data) {
    const channels = (data.channels || []).filter(c => !c.found || c.missing.length).length;
    const feeds = (data.feeds || []).filter(f => f.failures).length;
    const tasks = (data.tasks || []).filter(t => t.lastFailure && (!t.lastSuccess || t.lastFailure > t.lastSuccess)).length;
    const accounts = (data.accounts || []).filter(a => !a.connected).length;
    return channels + feeds + tasks + accounts;
}

export async function loadHealth(serverId) {
    const container = document.getElementById('health-report');
    if (!container) return;
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(serverId)}/health`);
    } catch (e) {
        container.innerHTML = html`<p class="u-c-text-muted">Diagnostic indisponible.</p>`;
        state.healthProblems = { ...(state.healthProblems || {}), [serverId]: null };
        refreshOverview(serverId);
        return;
    }
    state.healthProblems = { ...(state.healthProblems || {}), [serverId]: healthProblemCount(data) };
    refreshOverview(serverId);
    const section = (title, body) => String(body) && html`<h4 class="u-m-12-0-6">${title}</h4>${body}`;
    container.innerHTML = html`${section('Comptes', healthAccountsHtml(data.accounts))}${
        section('Salons', healthChannelsHtml(data.channels))}${
        section('Flux RSS', healthFeedsHtml(data.feeds))}${
        section('Tâches (pour tous les serveurs)', healthTasksHtml(data.tasks || []))}`;
}
