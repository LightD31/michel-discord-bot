// The server page's "Incidents" card (P21): the last failures, by reference.
import { VALUE, actionArgs, registerActions } from '../core/actions.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { UNAVAILABLE, cardHtml, loadCard, noteHtml } from '../ui/card.js';

export function incidentsCardHtml(sid) {
    return cardHtml({
        id: 'incidents-card',
        bodyId: 'incidents-list',
        title: 'Incidents',
        subtitle: 'Les derniers échecs de commandes, boutons et tâches sur ce serveur. Un membre qui rencontre une erreur reçoit sa référence : cherche-la ici.',
        tools: html`<input type="search" class="form-input incidents-search" placeholder="Référence (ex. ab12cd)" maxlength="12"
                    aria-label="Chercher une référence" value="${state.incidentSearch?.[sid] || ''}"
                    data-input="filterIncidents" data-input-args="${actionArgs(sid, VALUE)}">`,
    });
}

function when(epoch) {
    return new Date(epoch * 1000).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function incidentsListHtml(sid) {
    const all = state.incidents?.[sid];
    if (!all) return noteHtml(UNAVAILABLE);
    const wanted = (state.incidentSearch?.[sid] || '').trim().toLowerCase();
    const shown = wanted ? all.filter(i => i.reference.toLowerCase().includes(wanted)) : all;
    if (!all.length) return noteHtml('Aucun incident depuis le dernier démarrage de Michel. 🎉');
    if (!shown.length) return noteHtml(`Aucun incident avec la référence « ${wanted} » : elle date peut-être d'avant le dernier redémarrage.`);
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
    const keep = (list) => { state.incidents = { ...(state.incidents || {}), [sid]: list }; };
    const data = await loadCard('incidents-list', `/api/servers/${encodeURIComponent(sid)}/incidents`, (d) => {
        keep(d.incidents);
        return incidentsListHtml(sid);
    });
    if (data === null) keep(null);
}

export function filterIncidents(sid, value) {
    state.incidentSearch = { ...(state.incidentSearch || {}), [sid]: value };
    refresh(sid);
}

registerActions({ filterIncidents });
