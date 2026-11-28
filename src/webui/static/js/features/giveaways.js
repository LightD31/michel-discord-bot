// The running giveaways on their module page (P42): what /concours liste shows,
// and /concours tirer and /concours annuler as buttons.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { SPINNER, cardHtml, loadCard } from '../ui/card.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

const BODY = 'giveaways-running';
const base = sid => `/api/servers/${encodeURIComponent(sid)}/giveaways`;

export function giveawaysCardHtml(sid) {
    return cardHtml({
        className: 'u-mt-16',
        bodyId: BODY,
        title: '🎁 Giveaways en cours',
        subtitle: 'Les tirages ouverts, comme /concours liste ; les boutons font /concours tirer et annuler.',
        tools: html`<button class="btn btn-ghost btn-sm" data-click="loadGiveaways" data-click-args="${actionArgs(sid)}">↻ Actualiser</button>`,
    });
}

export function giveawayFacts(g) {
    const ends = new Date(g.ends_at).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
    const parts = [
        g.channel ? `#${g.channel}` : 'salon introuvable',
        `par ${g.host}`,
        `${g.entrants} participant${g.entrants > 1 ? 's' : ''}`,
        `${g.winners} gagnant${g.winners > 1 ? 's' : ''}`,
        `fin le ${ends}`,
    ];
    if (g.role) parts.push(`rôle ${g.role}`);
    if (g.min_level) parts.push(`niveau ${g.min_level} ou plus`);
    return parts.join(' · ');
}

export function giveawaysHtml(sid, data) {
    const off = !data.enabled
        ? html`<p class="me-empty">Le module est désactivé : rien n'est tiré à l'heure dite tant qu'il ne l'est pas, et les boutons attendent qu'il le soit.</p>`
        : '';
    return html`${off}<ul class="badge-stats">${data.giveaways.map(g => html`
        <li class="badge-stat giveaway-row">
            <div class="me-row-main">
                <div class="me-row-title"><a href="${g.url}" target="_blank" rel="noopener noreferrer">${g.prize}</a></div>
                <p class="me-row-sub">${giveawayFacts(g)}</p>
            </div>
            <div class="giveaway-actions">
                <button class="btn btn-primary btn-sm" data-click="drawGiveaway" data-click-args="${actionArgs(sid, g.id, g.prize, EL)}" ${data.enabled ? '' : 'disabled'}>🎲 Tirer maintenant</button>
                <button class="btn btn-ghost btn-sm" data-click="cancelGiveaway" data-click-args="${actionArgs(sid, g.id, g.prize, EL)}" ${data.enabled ? '' : 'disabled'}>Annuler</button>
            </div>
        </li>`)}</ul>`;
}

export async function loadGiveaways(sid) {
    const el = document.getElementById(BODY);
    if (el) el.innerHTML = SPINNER;
    await loadCard(BODY, base(sid), data => giveawaysHtml(sid, data), {
        empty: 'Aucun giveaway en cours. Lances-en un sur Discord avec /concours lancer.',
        isEmpty: data => !data.giveaways.length,
    });
}

async function act(sid, id, path, btn, done) {
    if (btn) btn.disabled = true;
    try {
        const result = await api(`${base(sid)}/${encodeURIComponent(id)}/${path}`, { method: 'POST' });
        toast(done(result), 'success');
    } catch (e) { /* handled by api() */ }
    finally {
        if (btn) btn.disabled = false;
        loadGiveaways(sid);
    }
}

export async function drawGiveaway(sid, id, prize, btn) {
    if (!(await askConfirm(`Les gagnants de « ${prize} » sont tirés tout de suite et annoncés dans son salon. Les inscriptions ferment.`, { title: 'Tirer maintenant ?', confirm: '🎲 Tirer' }))) return;
    await act(sid, id, 'draw', btn, r => (r.winners.length
        ? `Tirage effectué : ${r.winners.join(', ')}.`
        : 'Tirage effectué : aucune participation valide.'));
}

export async function cancelGiveaway(sid, id, prize, btn) {
    if (!(await askConfirm(`« ${prize} » est annulé sans tirage, et son annonce le dit. On ne peut pas revenir en arrière.`, { title: 'Annuler le giveaway ?', confirm: 'Annuler le giveaway', danger: true }))) return;
    await act(sid, id, 'cancel', btn, () => 'Giveaway annulé.');
}

registerActions({
    cancelGiveaway,
    drawGiveaway,
    loadGiveaways,
});
