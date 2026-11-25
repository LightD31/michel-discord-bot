// The agenda's calendar on the server page (P19).
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { cardHtml, loadCard } from '../ui/card.js';

const WEEKDAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
const SOURCES = { event: 'événements Discord', birthday: 'anniversaires', giveaway: 'giveaways', poll: 'sondages', santa: 'Père Noël secret' };

export function agendaOn(sid) {
    return state.servers?.[sid]?.config?.moduleAgenda?.enabled === true;
}

// The card's frame; loadAgenda() fills it.
export function agendaCardHtml(sid) {
    if (!agendaOn(sid)) return '';
    return cardHtml({
        id: 'agenda-card',
        bodyId: 'agenda-body',
        title: '🗓️ Agenda',
        subtitle: 'Ce que montre /agenda : événements, anniversaires, fins de giveaways et de sondages, Père Noël secret.',
    });
}

function isoDay(date) {
    const y = date.getFullYear(), m = String(date.getMonth() + 1).padStart(2, '0'), d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function entryHtml(e) {
    const text = html`${e.time && html`<span class="agenda-time">${e.time}</span> `}${e.title}`;
    return html`<li class="agenda-entry agenda-${e.kind}"><span aria-hidden="true">${e.icon}</span>
        ${e.url ? html`<a href="${e.url}" target="_blank" rel="noopener">${text}</a>` : text}</li>`;
}

// Whole weeks from the Monday of today to the last day shown; days outside are blank.
export function agendaGridHtml(data) {
    const byDay = new Map();
    for (const e of data.entries) {
        if (!byDay.has(e.day)) byDay.set(e.day, []);
        byDay.get(e.day).push(e);
    }
    const [y, m, d] = data.today.split('-').map(Number);
    const today = new Date(y, m - 1, d);
    const last = new Date(y, m - 1, d + data.days);
    const start = new Date(y, m - 1, d - ((today.getDay() + 6) % 7));
    const cells = [];
    for (let day = new Date(start); day <= last || cells.length % 7; day.setDate(day.getDate() + 1)) {
        const iso = isoDay(day);
        const inRange = day >= today && day <= last;
        const entries = byDay.get(iso) || [];
        const classes = ['agenda-day', inRange ? '' : 'is-out', iso === data.today ? 'is-today' : '', entries.length ? 'has-entries' : ''].filter(Boolean).join(' ');
        cells.push(html`
            <div class="${classes}">
                <div class="agenda-date">${entries[0]?.dayLabel || day.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })}</div>
                <div class="agenda-num" aria-hidden="true">${day.getDate()}</div>
                ${entries.length ? html`<ul>${entries.map(entryHtml)}</ul>` : ''}
            </div>`);
    }
    const missing = (data.unavailable || []).map(k => SOURCES[k] || k);
    return html`
        ${missing.length ? html`<p class="u-c-text-muted u-fs-13">Indisponible pour le moment : ${missing.join(', ')}.</p>` : ''}
        ${data.entries.length ? '' : html`<p class="me-empty">Rien de prévu sur les ${data.days} prochains jours.</p>`}
        <div class="agenda-grid">
            ${WEEKDAYS.map(w => html`<div class="agenda-weekday" aria-hidden="true">${w}</div>`)}
            ${cells}
        </div>`;
}

export async function loadAgenda(sid) {
    await loadCard('agenda-body', `/api/servers/${encodeURIComponent(sid)}/agenda`, agendaGridHtml);
}
