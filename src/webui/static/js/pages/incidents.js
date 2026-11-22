// The server page's "Incidents" card (P21): the last failures, by reference.
import { VALUE, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';

export function incidentsCardHtml(sid) {
    return html`
        <div class="card" id="incidents-card">
            <div class="card-header">
                <div>
                    <div class="card-title">Incidents</div>
                    <div class="card-subtitle">Les derniers échecs de commandes, boutons et tâches sur ce serveur. Un membre qui rencontre une erreur reçoit sa référence : cherche-la ici.</div>
                </div>
                <input type="search" class="form-input incidents-search" placeholder="Référence (ex. ab12cd)" maxlength="12"
                    aria-label="Chercher une référence" value="${state.incidentSearch?.[sid] || ''}"
                    data-input="filterIncidents" data-input-args="${actionArgs(sid, VALUE)}">
            </div>
            <div id="incidents-list"><div class="loading"><div class="spinner"></div></div></div>
        </div>`;
}

function when(epoch) {
    return new Date(epoch * 1000).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function incidentsListHtml(sid) {
    const all = state.incidents?.[sid];
    if (!all) return html`<p class="me-empty">Les incidents sont indisponibles pour le moment.</p>`;
    const wanted = (state.incidentSearch?.[sid] || '').trim().toLowerCase();
    const shown = wanted ? all.filter(i => i.reference.toLowerCase().includes(wanted)) : all;
    if (!all.length) return html`<p class="me-empty">Aucun incident depuis le dernier démarrage de Michel. 🎉</p>`;
    if (!shown.length) return html`<p class="me-empty">Aucun incident avec la référence « ${wanted} » : elle date peut-être d'avant le dernier redémarrage.</p>`;
    return html`<ul class="incidents">${shown.map(i => html`
        <li class="incident">
            <code class="incident-ref">${i.reference}</code>
            <div class="incident-main">
                <div class="incident-where">${i.where}</div>
                <div class="incident-reason">${i.reason}</div>
            </div>
            <time class="incident-when">${when(i.at)}</time>
        </li>`)}</ul>`;
}

function refresh(sid) {
    const el = document.getElementById('incidents-list');
    if (el && state.currentServerId === sid) el.innerHTML = String(incidentsListHtml(sid));
}

export async function loadIncidents(sid) {
    if (!document.getElementById('incidents-list')) return;
    let data = null;
    try {
        data = await api(`/api/servers/${encodeURIComponent(sid)}/incidents`);
    } catch (e) { /* shown as unavailable */ }
    state.incidents = { ...(state.incidents || {}), [sid]: data ? data.incidents : null };
    refresh(sid);
}

export function filterIncidents(sid, value) {
    state.incidentSearch = { ...(state.incidentSearch || {}), [sid]: value };
    refresh(sid);
}

registerActions({ filterIncidents });
