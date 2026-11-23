// The member space, "Mon espace" (C6): what Michel keeps about the member, and
// what they can set themselves, server by server. Every request goes to
// /api/me/… (src/webui/routes/me.py), which acts on the session's own user:
// nothing here names a member, only a reminder, a group or the person paid back.
import { EL, VALUE, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { setDirty } from '../core/dirty.js';
import { html } from '../core/html.js';
import { navigate, routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { agendaGridHtml } from './agenda.js';

const FREQUENCIES = {
    '': 'Une seule fois',
    daily: 'Tous les jours',
    weekly: 'Toutes les semaines',
    monthly: 'Tous les mois',
    yearly: 'Tous les ans',
};
// Offered while typing a time zone; any IANA name is accepted (checked by the server).
const TIMEZONES = [
    'Europe/Paris', 'Europe/Brussels', 'Europe/Zurich', 'Europe/Luxembourg', 'Europe/London',
    'America/Montreal', 'America/Toronto', 'America/New_York', 'America/Los_Angeles',
    'America/Guadeloupe', 'America/Martinique', 'America/Cayenne', 'Indian/Reunion',
    'Indian/Mayotte', 'Pacific/Noumea', 'Pacific/Tahiti', 'Africa/Casablanca', 'Africa/Dakar',
    'Africa/Abidjan', 'Asia/Tokyo',
];

function me() {
    if (!state.me) state.me = { servers: null, data: {}, editing: null };
    return state.me;
}

function money(cents, currency) {
    const amount = (cents / 100).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${amount} ${currency || '€'}`;
}

function thousands(n) {
    return Number(n || 0).toLocaleString('fr-FR');
}

// "dans 2h" reads back what the bot understood; a stored reminder is typed back as "24/12 20h30".
function whenText(iso) {
    const d = new Date(iso);
    const parts = new Intl.DateTimeFormat('fr-FR', {
        timeZone: 'Europe/Paris', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(d);
    const get = (type) => parts.find(p => p.type === type)?.value || '';
    return `${get('day')}/${get('month')} ${get('hour')}h${get('minute')}`;
}

// ── Page ──────────────────────────────────────────
export function renderMePage() {
    const space = me();
    const servers = space.servers;
    const count = servers?.length || 0;
    const header = html`
        <div class="page-header">
            <h2>Salut ${state.user?.username || ''} !</h2>
            <p>Ce que Michel sait de toi et ce que tu peux régler toi-même${count > 1 ? `, sur tes ${count} serveurs` : ''}.</p>
        </div>`;
    if (servers === null) {
        return html`${header}<div class="loading"><div class="spinner"></div> Chargement…</div>`;
    }
    const sid = state.currentServerId;
    const tabs = count > 1 ? html`
        <div class="me-tabs" role="tablist" aria-label="Serveur">
            ${servers.map(s => html`<a class="me-tab ${s.id === sid ? 'active' : ''}" role="tab" aria-selected="${String(s.id === sid)}"
                href="${routeHash('me', s.id)}" data-click="navigate" data-click-args="${actionArgs('me', s.id)}" data-prevent>${s.name}</a>`)}
        </div>` : '';
    const body = count === 0
        ? html`<div class="card"><p class="me-empty">Tu ne partages aucun serveur avec Michel pour l'instant.
            Quand tu en rejoins un, ce que tu y règles s'affiche ici.</p></div>`
        : html`<div id="me-body">${serverHtml(sid)}</div>`;
    return html`${header}${tabs}${body}${dataCardHtml()}`;
}

function serverHtml(sid) {
    const server = me().servers?.find(s => s.id === sid);
    const data = me().data[sid];
    if (!server) return '';
    if (data === undefined) return html`<div class="loading"><div class="spinner"></div> Chargement…</div>`;
    if (data === null) return html`<div class="card"><p class="me-empty">Ce serveur ne répond pas pour l'instant : réessaie dans un instant.</p></div>`;
    const cards = [
        data.sections.includes('xp') && xpCardHtml(server, data),
        data.sections.includes('badges') && badgesCardHtml(data),
        data.sections.includes('agenda') && agendaCardHtml(data),
        data.sections.includes('reminders') && remindersCardHtml(sid, data),
        data.sections.includes('birthday') && birthdayCardHtml(sid, data),
        data.sections.includes('tricount') && tricountCardHtml(sid, data),
        data.sections.includes('santa') && santaCardHtml(sid, data),
    ].filter(Boolean);
    if (!cards.length) {
        return html`<div class="card"><p class="me-empty">Rien à régler sur ${server.name} pour l'instant :
            ses administrateurs n'y ont activé aucun module qui te concerne.</p></div>`;
    }
    return html`<div class="me-grid">${cards}</div>`;
}

function unavailable(data, section) {
    return data.unavailable?.includes(section)
        ? html`<p class="me-empty">Indisponible pour le moment : réessaie dans un instant.</p>`
        : null;
}

// ── XP ────────────────────────────────────────────
function xpCardHtml(server, data) {
    const xp = data.xp;
    const tile = (label, value) => html`<div class="me-tile"><div class="me-tile-label">${label}</div><div class="me-tile-value">${value}</div></div>`;
    const inner = unavailable(data, 'xp') || html`
        <div class="me-tiles">
            ${tile('Niveau', String(xp.level))}
            ${tile('Rang', xp.rank ? html`${xp.rank}<sup>e</sup> / ${thousands(xp.ranked)}` : '—')}
            ${xp.season ? tile('Saison', `+${thousands(xp.season.xp)} XP`) : tile('XP', thousands(xp.xp))}
        </div>
        ${xp.season && html`<p class="me-row-sub">Saison ${xp.season.label} · ${thousands(xp.xp)} XP en tout</p>`}
        <p class="me-progress-label">${thousands(xp.xpInLevel)} / ${thousands(xp.xpToNext)} XP avant le niveau ${xp.level + 1}</p>
        <div class="progress" role="progressbar" aria-label="Progression vers le niveau ${xp.level + 1}"
            aria-valuemin="0" aria-valuemax="${xp.xpToNext}" aria-valuenow="${xp.xpInLevel}">
            <div class="progress-fill" style="width:${Math.min(100, Math.round((xp.xpInLevel / Math.max(1, xp.xpToNext)) * 100))}%"></div>
        </div>`;
    return html`<div class="card me-card" id="me-xp"><div class="card-title">${server.name}</div>${inner}</div>`;
}

// ── Agenda (P25) ──────────────────────────────────
// Gathered on the bot loop by its own route: the card fills itself once shown.
const _agendas = {};  // sid -> the agenda last read, so a re-render keeps it

function agendaCardHtml(data) {
    const cached = _agendas[state.currentServerId];
    return html`<div class="card me-card me-agenda" id="me-agenda"><div class="card-title">🗓️ Les ${data.agenda?.days ?? 14} prochains jours</div>
        <div id="me-agenda-body">${cached ? agendaGridHtml(cached) : html`<div class="loading"><div class="spinner"></div></div>`}</div></div>`;
}

export async function loadMeAgenda(sid) {
    const el = document.getElementById('me-agenda-body');
    if (!el) return;
    let data;
    try {
        data = await api(`${base(sid)}/agenda`);
    } catch (e) {
        el.innerHTML = String(html`<p class="me-empty">Indisponible pour le moment : réessaie dans un instant.</p>`);
        return;
    }
    _agendas[sid] = data;
    if (document.getElementById('me-agenda-body') === el) el.innerHTML = String(agendaGridHtml(data));
}

// ── Badges (P18) ──────────────────────────────────
function badgesCardHtml(data) {
    const all = data.badges || [];
    const won = all.filter(b => b.earnedAt);
    const row = b => html`
        <div class="me-badge ${b.earnedAt ? 'is-won' : ''}">
            <span class="me-badge-emoji" aria-hidden="true">${b.emoji}</span>
            <div class="me-row-main">
                <div class="me-row-title">${b.name}</div>
                <p class="me-row-sub">${b.earnedAt ? `Débloqué le ${new Date(b.earnedAt).toLocaleDateString('fr-FR')}` : `À débloquer : ${b.goal}`}</p>
            </div>
        </div>`;
    const inner = unavailable(data, 'badges') || (all.length
        ? html`<p class="me-row-sub">${won.length} sur ${all.length} · vérifiés toutes les heures</p>
               <div class="me-badges">${[...won, ...all.filter(b => !b.earnedAt)].map(row)}</div>`
        : html`<p class="me-empty">Aucun badge actif sur ce serveur.</p>`);
    return html`<div class="card me-card" id="me-badges"><div class="card-title">🏆 Succès</div>${inner}</div>`;
}

// ── Reminders ─────────────────────────────────────
function reminderFormHtml(sid, reminder) {
    const id = reminder?.id || '';
    const options = Object.entries(FREQUENCIES).map(([value, label]) =>
        html`<option value="${value}" ${(reminder?.frequency || '') === value ? 'selected' : ''}>${label}</option>`);
    return html`
        <div class="me-form" id="me-reminder-form">
            <label class="form-label" for="me-reminder-message">Quoi ?</label>
            <textarea class="form-textarea" id="me-reminder-message" maxlength="1500" rows="2">${reminder?.message || ''}</textarea>
            <label class="form-label" for="me-reminder-when">Quand ?</label>
            <input class="form-input" id="me-reminder-when" maxlength="60" autocomplete="off"
                placeholder="dans 2h, demain 18h30, samedi 9h, 24/12 20h"
                value="${reminder ? whenText(reminder.at) : ''}" data-input="meWhenTyped" data-input-args="${actionArgs(VALUE)}"
                aria-describedby="me-reminder-reading">
            <p class="form-range" id="me-reminder-reading" aria-live="polite"></p>
            <label class="form-label" for="me-reminder-frequency">Répéter</label>
            <select class="form-input" id="me-reminder-frequency">${options}</select>
            <div class="me-actions">
                <button type="button" class="btn btn-ghost btn-sm" data-click="meCancelReminder">Annuler</button>
                <button type="button" class="btn btn-primary btn-sm" data-click="meSaveReminder" data-click-args="${actionArgs(sid, id, EL)}">${id ? 'Enregistrer' : 'Créer le rappel'}</button>
            </div>
        </div>`;
}

function remindersCardHtml(sid, data) {
    const editing = me().editing;
    const reminders = data.reminders;
    let inner = unavailable(data, 'reminders');
    if (!inner) {
        const rows = reminders.items.map(r => editing === r.id ? reminderFormHtml(sid, r) : html`
            <li class="me-row">
                <div class="me-row-main">
                    <div class="me-row-title">${r.message}</div>
                    <div class="me-row-sub">${r.label}${r.jumpUrl && html` · <a href="${r.jumpUrl}" target="_blank" rel="noopener noreferrer">le message</a>`}</div>
                </div>
                <div class="me-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-click="meEditReminder" data-click-args="${actionArgs(r.id)}"
                        aria-label="Modifier le rappel « ${r.message} »">✏️</button>
                    <button type="button" class="btn btn-ghost btn-sm btn-ghost-danger" data-click="meDeleteReminder" data-click-args="${actionArgs(sid, r.id, EL)}"
                        aria-label="Supprimer le rappel « ${r.message} »">🗑️</button>
                </div>
            </li>`);
        const full = reminders.items.length >= reminders.limit;
        inner = html`
            ${reminders.items.length
                ? html`<ul class="me-list">${rows}</ul>`
                : html`<p class="me-empty">Aucun rappel : Michel t'écrit en MP à l'heure dite.</p>`}
            ${editing === 'new'
                ? reminderFormHtml(sid, null)
                : html`<button type="button" class="btn btn-primary btn-sm" data-click="meNewReminder" ${full ? 'disabled' : ''}>+ Nouveau rappel</button>
                    ${full && html`<p class="form-range">Tu as atteint ${reminders.limit} rappels sur ce serveur.</p>`}`}`;
    }
    return html`<div class="card me-card" id="me-reminders"><div class="card-title">⏰ Mes rappels</div>${inner}</div>`;
}

// ── Birthday ──────────────────────────────────────
function birthdayCardHtml(sid, data) {
    const b = data.birthday;
    const inner = unavailable(data, 'birthday') || html`
        <p class="me-row-sub">${b
            ? html`Michel te le souhaite le <strong>${b.label}</strong>${b.hasYear && b.hideYear ? ' (ton âge reste caché)' : ''}.`
            : 'Donne ta date : Michel te le souhaite le jour J. L\'année est facultative.'}</p>
        <div class="me-form">
            <label class="form-label" for="me-birthday-date">Date</label>
            <input class="form-input" id="me-birthday-date" maxlength="40" autocomplete="off" placeholder="12/03, 12/03/1990 ou 12 mars" value="${b?.date || ''}">
            <label class="form-label" for="me-birthday-tz">Fuseau horaire</label>
            <input class="form-input" id="me-birthday-tz" list="me-timezones" maxlength="64" value="${b?.timezone || 'Europe/Paris'}">
            <datalist id="me-timezones">${TIMEZONES.map(tz => html`<option value="${tz}"></option>`)}</datalist>
            <label class="me-check"><input type="checkbox" id="me-birthday-hide" ${b?.hideYear ? 'checked' : ''}> Cacher mon âge dans la liste des anniversaires</label>
            <div class="me-actions">
                ${b && html`<button type="button" class="btn btn-ghost btn-sm btn-ghost-danger" data-click="meDeleteBirthday" data-click-args="${actionArgs(sid, EL)}">Retirer</button>`}
                <button type="button" class="btn btn-primary btn-sm" data-click="meSaveBirthday" data-click-args="${actionArgs(sid, EL)}">Enregistrer</button>
            </div>
        </div>`;
    return html`<div class="card me-card" id="me-birthday"><div class="card-title">🎂 Mon anniversaire</div>${inner}</div>`;
}

// ── Tricount ──────────────────────────────────────
function tricountCardHtml(sid, data) {
    const groups = data.tricount;
    const name = (id) => data.names?.[id] || 'un membre';
    let inner = unavailable(data, 'tricount');
    if (!inner) {
        inner = groups.length ? groups.map(g => html`
            <div class="me-group">
                <div class="me-row-title">« ${g.name} »</div>
                ${g.owes.map(line => html`
                    <div class="me-row">
                        <div class="me-row-main">Tu dois <strong>${money(line.cents, data.currency)}</strong> à ${name(line.to)}</div>
                        ${settleControls(sid, g.id, line.to, line.cents, 'paid', `à ${name(line.to)}`)}
                    </div>`)}
                ${g.owed.map(line => html`
                    <div class="me-row">
                        <div class="me-row-main">${name(line.from)} te doit <strong>${money(line.cents, data.currency)}</strong></div>
                        ${settleControls(sid, g.id, line.from, line.cents, 'received', `de ${name(line.from)}`)}
                    </div>`)}
                ${!g.owes.length && !g.owed.length && html`<div class="me-row-sub">Tu es à jour ✅</div>`}
            </div>`)
            : html`<p class="me-empty">Aucun compte en cours. Un groupe se crée avec <code>/tricount groupe</code>.</p>`;
    }
    return html`<div class="card me-card" id="me-tricount"><div class="card-title">💰 Mes comptes</div>${inner}</div>`;
}

// A line of the plan: its amount (all by default, less for part of it) and the button (P16).
function settleControls(sid, groupId, other, cents, direction, who) {
    const max = (cents / 100).toFixed(2);
    return html`
        <div class="me-settle">
            <input type="number" class="form-input me-settle-amount" min="0.01" max="${max}" step="0.01" value="${max}"
                aria-label="Montant ${direction === 'paid' ? 'remboursé' : 'reçu'} ${who}">
            <button type="button" class="btn ${direction === 'paid' ? 'btn-success' : 'btn-secondary'} btn-sm" data-click="meSettle"
                data-click-args="${actionArgs(sid, groupId, other, cents, direction, who, EL)}">${direction === 'paid' ? 'J\'ai remboursé' : 'On m\'a remboursé'}</button>
        </div>`;
}

// ── Secret Santa ──────────────────────────────────
function santaCardHtml(sid, data) {
    const s = data.santa;
    let inner = unavailable(data, 'santa');
    if (!inner) {
        if (!s) {
            inner = html`<p class="me-empty">Pas de Père Noël Secret en cours sur ce serveur.</p>`;
        } else {
            const when = [s.exchangeLabel && `Échange le ${s.exchangeLabel}`, s.budget && `budget ${s.budget}`, `${s.participants} participant${s.participants > 1 ? 's' : ''}`]
                .filter(Boolean).join(' · ');
            let action;
            if (!s.participant) {
                action = s.drawn
                    ? html`<p class="me-empty">Le tirage est fait : les inscriptions sont closes.</p>`
                    : html`<div class="me-actions"><button type="button" class="btn btn-primary btn-sm" data-click="meSantaJoin" data-click-args="${actionArgs(sid, EL)}">🎁 Participer</button></div>`;
            } else if (s.drawn) {
                action = html`<p class="me-row-sub">Le tirage est fait : regarde tes MP pour savoir à qui tu offres.</p>
                    ${s.wishlist && html`<p class="me-row-sub">Ta liste : ${s.wishlist}</p>`}`;
            } else {
                action = html`
                    <div class="me-form">
                        <label class="form-label" for="me-wishlist">Ma liste d'envies</label>
                        <textarea class="form-textarea" id="me-wishlist" maxlength="1000" rows="3"
                            placeholder="Des idées pour ton Père Noël : un livre, un jeu…">${s.wishlist}</textarea>
                        <div class="me-actions">
                            <button type="button" class="btn btn-primary btn-sm" data-click="meSaveWishlist" data-click-args="${actionArgs(sid, EL)}">Enregistrer ma liste</button>
                            <button type="button" class="btn btn-ghost btn-sm" data-click="meSantaLeave" data-click-args="${actionArgs(sid, EL)}">Me retirer</button>
                        </div>
                    </div>`;
            }
            inner = html`<p class="me-row-sub">${when}</p>${action}`;
        }
    }
    return html`<div class="card me-card" id="me-santa"><div class="card-title">🎁 Père Noël secret</div>${inner}</div>`;
}

// ── My data ───────────────────────────────────────
function dataCardHtml() {
    return html`
        <div class="card" id="me-data">
            <div class="card-title">🗂️ Mes données</div>
            <p class="me-row-sub">Ce que Michel garde sur toi, sur tous les serveurs : en recevoir une copie, ou tout effacer
                (comme <code>/mes-donnees</code> dans Discord).</p>
            <div class="me-actions me-actions-start">
                <button type="button" class="btn btn-ghost btn-sm" data-click="meExportData" data-click-args="${actionArgs(EL)}">⬇️ Télécharger mes données</button>
                <button type="button" class="btn btn-ghost btn-sm btn-ghost-danger" data-click="meDeleteData" data-click-args="${actionArgs(EL)}">Tout effacer…</button>
            </div>
            <div id="me-data-result" role="status"></div>
        </div>`;
}

// ── Loading ───────────────────────────────────────
function refreshBody() {
    if (state.currentPage !== 'me') return;
    const main = document.querySelector('.main');
    if (main) main.innerHTML = renderMePage();
}

export async function loadMe(sid) {
    const space = me();
    if (space.servers === null) {
        try {
            space.servers = (await api('/api/me/servers'))?.servers || [];
        } catch (e) {
            space.servers = [];
        }
        // No server chosen (or one I'm not in): the first one.
        if (space.servers.length && !space.servers.some(s => s.id === sid)) {
            navigate('me', space.servers[0].id, null, { fromHistory: true });
            return;
        }
        refreshBody();
    }
    if (sid && space.servers.some(s => s.id === sid)) await loadMeServer(sid);
}

async function loadMeServer(sid) {
    let data;
    try {
        data = await api(`/api/me/servers/${encodeURIComponent(sid)}`);
    } catch (e) {
        data = null;
    }
    me().data[sid] = data;
    if (state.currentServerId === sid) refreshBody();
    if (data?.sections?.includes('agenda')) loadMeAgenda(sid);
}

async function write(btn, url, options, success) {
    if (btn) btn.disabled = true;
    try {
        const result = await api(url, options);
        if (success) toast(success, 'success');
        setDirty(false);  // saved: nothing typed is left to lose (P16)
        return result ?? true;
    } catch (e) {
        if (btn) btn.disabled = false;
        return null;
    }
}

const base = (sid) => `/api/me/servers/${encodeURIComponent(sid)}`;

// ── Actions ───────────────────────────────────────
export function meNewReminder() {
    me().editing = 'new';
    refreshBody();
    document.getElementById('me-reminder-message')?.focus();
}

export function meEditReminder(id) {
    me().editing = id;
    refreshBody();
    document.getElementById('me-reminder-message')?.focus();
}

export function meCancelReminder() {
    me().editing = null;
    setDirty(false);  // given up on purpose
    refreshBody();
}

let whenTimer = null;
export function meWhenTyped(value) {
    clearTimeout(whenTimer);
    whenTimer = setTimeout(async () => {
        const out = document.getElementById('me-reminder-reading');
        if (!out) return;
        if (!value.trim()) { out.textContent = ''; return; }
        try {
            const found = await api(`/api/me/when?q=${encodeURIComponent(value)}`, { quiet: true });
            out.textContent = found?.reading ? `→ ${found.reading}` : `❓ ${found?.error || ''}`;
        } catch (e) { /* says nothing: the save answers */ }
    }, 300);
}

export async function meSaveReminder(sid, id, btn) {
    const body = {
        message: document.getElementById('me-reminder-message')?.value.trim() || '',
        when: document.getElementById('me-reminder-when')?.value.trim() || '',
        frequency: document.getElementById('me-reminder-frequency')?.value || null,
    };
    if (!body.message || !body.when) {
        toast('Dis quoi et quand : par exemple « Sortir le chien », « ce soir 20h30 ».', 'error');
        return;
    }
    const url = id ? `${base(sid)}/reminders/${encodeURIComponent(id)}` : `${base(sid)}/reminders`;
    const ok = await write(btn, url, { method: id ? 'PUT' : 'POST', body: JSON.stringify(body) },
        id ? 'Rappel modifié.' : 'Rappel créé : Michel t\'écrira en MP.');
    if (!ok) return;
    me().editing = null;
    await loadMeServer(sid);
}

export async function meDeleteReminder(sid, id, btn) {
    if (!(await askConfirm('Supprimer ce rappel ?', { title: 'Supprimer le rappel', confirm: 'Supprimer', danger: true }))) return;
    if (await write(btn, `${base(sid)}/reminders/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Rappel supprimé.')) {
        await loadMeServer(sid);
    }
}

export async function meSaveBirthday(sid, btn) {
    const body = {
        date: document.getElementById('me-birthday-date')?.value.trim() || '',
        timezone: document.getElementById('me-birthday-tz')?.value.trim() || 'Europe/Paris',
        hideYear: !!document.getElementById('me-birthday-hide')?.checked,
    };
    if (!body.date) {
        toast('Donne ta date : par exemple 12/03 ou 12/03/1990.', 'error');
        return;
    }
    if (await write(btn, `${base(sid)}/birthday`, { method: 'PUT', body: JSON.stringify(body) }, 'Anniversaire enregistré.')) {
        await loadMeServer(sid);
    }
}

export async function meDeleteBirthday(sid, btn) {
    if (!(await askConfirm('Michel ne te souhaitera plus ton anniversaire sur ce serveur.',
        { title: 'Retirer ton anniversaire', confirm: 'Retirer', danger: true }))) return;
    if (await write(btn, `${base(sid)}/birthday`, { method: 'DELETE' }, 'Anniversaire retiré.')) {
        await loadMeServer(sid);
    }
}

export async function meSettle(sid, groupId, to, cents, direction, who, btn) {
    const typed = Math.round(Number.parseFloat(btn?.parentElement?.querySelector('.me-settle-amount')?.value || '') * 100);
    if (!Number.isInteger(typed) || typed < 1 || typed > cents) {
        toast(`Le montant va de 0,01 à ${(cents / 100).toLocaleString('fr-FR', { minimumFractionDigits: 2 })}.`, 'error');
        return;
    }
    const amount = (typed / 100).toLocaleString('fr-FR', { minimumFractionDigits: 2 });
    const question = direction === 'paid'
        ? `Enregistrer que tu as remboursé ${amount} ${who} ?`
        : `Enregistrer qu'on t'a remboursé ${amount} ${who} ?`;
    if (!(await askConfirm(`${question} Le bilan du groupe sera mis à jour.`,
        { title: direction === 'paid' ? 'J\'ai remboursé' : 'On m\'a remboursé', confirm: 'Enregistrer' }))) return;
    const ok = await write(btn, `${base(sid)}/tricount/${encodeURIComponent(groupId)}/settle`,
        { method: 'POST', body: JSON.stringify({ to, cents: typed, direction }) }, 'Remboursement enregistré.');
    if (ok) await loadMeServer(sid);
}

export async function meSantaJoin(sid, btn) {
    if (await write(btn, `${base(sid)}/santa/join`, { method: 'POST' },
        'Tu es inscrit·e au Père Noël Secret ! Écris ta liste d\'envies ci-dessous.')) {
        await loadMeServer(sid);
    }
}

export async function meSantaLeave(sid, btn) {
    if (!(await askConfirm('Tu ne participeras plus au Père Noël Secret, et ta liste d\'envies sera effacée.',
        { title: 'Me retirer', confirm: 'Me retirer', danger: true }))) return;
    if (await write(btn, `${base(sid)}/santa/leave`, { method: 'POST' }, 'Tu t\'es retiré·e du Père Noël Secret.')) {
        await loadMeServer(sid);
    }
}

export async function meSaveWishlist(sid, btn) {
    const text = document.getElementById('me-wishlist')?.value || '';
    const ok = await write(btn, `${base(sid)}/santa/wishlist`, { method: 'PUT', body: JSON.stringify({ text }) },
        text.trim() ? 'Liste enregistrée : ton Père Noël la recevra au tirage.' : 'Ta liste est vide.');
    if (ok) {
        btn.disabled = false;
        await loadMeServer(sid);
    }
}

export async function meExportData(btn) {
    btn.disabled = true;
    try {
        const resp = await fetch('/api/me/data/export', { method: 'POST' });
        if (!resp.ok) throw new Error(String(resp.status));
        const url = URL.createObjectURL(await resp.blob());
        const link = document.createElement('a');
        link.href = url;
        link.download = 'mes-donnees.json';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('Voici tout ce que Michel garde sur toi.', 'success');
    } catch (e) {
        toast('L\'export n\'a pas abouti : réessaie dans un instant.', 'error');
    } finally {
        btn.disabled = false;
    }
}

export async function meDeleteData(btn) {
    if (!(await askConfirm('Effacer ce que Michel garde sur toi, sur tous les serveurs ? Ton XP, ton anniversaire et tes '
        + 'rappels sont supprimés ; tes votes et ce que tu as proposé restent, sans ton nom. C\'est définitif.',
    { title: 'Tout effacer', confirm: 'Tout effacer', danger: true }))) return;
    const report = await write(btn, '/api/me/data/delete', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    if (!report) return;
    btn.disabled = false;
    const result = document.getElementById('me-data-result');
    if (result) {
        result.innerHTML = html`<p class="me-row-sub">🗑️ ${report.deleted} élément(s) supprimé(s), ${report.anonymised} anonymisé(s).</p>
            ${report.kept.length > 0 && html`<p class="me-row-sub">Conservé : ${report.kept.join(' ; ')}.</p>`}
            <p class="me-row-sub">${report.notCovered}</p>`;
    }
    me().data = {};
    if (state.currentServerId) await loadMeServer(state.currentServerId);
}

registerActions({
    meCancelReminder,
    meDeleteBirthday,
    meDeleteData,
    meDeleteReminder,
    meEditReminder,
    meExportData,
    meNewReminder,
    meSaveBirthday,
    meSaveReminder,
    meSantaJoin,
    meSantaLeave,
    meSaveWishlist,
    meSettle,
    meWhenTyped,
});
