// Secret Santa: the session and its draw.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html, raw } from '../core/html.js';
import { getPickerSelectedId, memberPickerHtml, populateMemberPickers } from '../forms/pickers/members.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Secret Santa: the session and its draw (P2) ─────────────────────
// Counts only once drawn: who sent or received a parcel would hint at pairs.
export function santaProgress(label, done, total) {
    const pct = total ? Math.round(100 * done / total) : 0;
    return String(html`
            <div class="santa-progress">
                <div class="santa-progress-label"><span>${raw(label)}</span><strong>${raw(done)}/${raw(total)}</strong></div>
                <div class="santa-progress-bar" role="progressbar" aria-label="${label}" aria-valuemin="0" aria-valuemax="${raw(total)}" aria-valuenow="${raw(done)}"><span style="width:${raw(pct)}%"></span></div>
            </div>`);
}

export function secretSantaHtml(serverId, s) {
    if (!s) {
        return `<div class="empty-state"><h3>Aucune session</h3><p>Crées-en une sur Discord avec <code>/secretsanta creer</code>, en présentiel ou par colis (option <code>colis</code>).</p></div>`;
    }
    const withList = s.participants.filter(p => p.has_wishlist).length;
    const facts = [
        ['Statut', s.is_drawn ? '✅ Tirage effectué' : '⏳ Inscriptions ouvertes'],
        ['Échange', s.exchange_label ? String(html`${s.exchange_label}`) : '<span class="u-c-text-muted">Pas de date</span>'],
        ['Budget', s.budget ? String(html`${s.budget}`) : '<span class="u-c-text-muted">Non précisé</span>'],
        ['Remise', s.shipping ? '📦 Par colis (suivi et rappel)' : '🤝 En main propre'],
        ['Organisé par', String(html`${s.created_by}`)],
    ];
    const people = s.participants.length
        ? `<ul class="santa-people">${s.participants.map(p => String(html`
                <li><span>${p.name}</span>${raw(p.has_wishlist ? '<span class="santa-list" title="A laissé une liste d\'envies">📝 liste</span>' : '')}</li>`)).join('')}</ul>`
        : '<p class="u-c-text-muted">Personne n\'est encore inscrit.</p>';
    const progress = s.is_drawn && s.shipping
        ? santaProgress('📦 Colis envoyés', s.sent || 0, s.total) + santaProgress('🎁 Colis reçus', s.received || 0, s.total)
        : (!s.is_drawn ? santaProgress('📝 Listes d\'envies', withList, s.total) : '');
    const draw = s.is_drawn ? '' : `
            <div class="santa-draw">
                <label class="santa-check"><input type="checkbox" id="santa-subgroups"> Autoriser plusieurs sous-groupes si les exclusions empêchent une boucle unique</label>
                <button class="btn btn-primary" data-click="drawSecretSanta" data-click-args="${actionArgs(serverId, EL)}" ${s.total < 2 ? 'disabled title="Il faut au moins 2 participants"' : ''}>🎲 Lancer le tirage</button>
            </div>`;
    // P15: who gives to whom, for the server's administrators only (the API checks it again).
    const reveal = s.can_reveal ? html`
            <div class="santa-reveal">
                <button class="btn btn-secondary" data-click="revealSecretSanta" data-click-args="${actionArgs(serverId, EL)}">🔍 Révéler les attributions</button>
                <div id="santa-pairs" hidden></div>
            </div>` : '';
    return `
            <dl class="santa-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
            ${progress}
            <h4 class="u-m-16-0-8">Participants (${s.total})</h4>
            ${people}
            ${draw}
            ${reveal}`;
}

export function santaPairsHtml(pairs) {
    return html`
            <p class="santa-reveal-note">Seulement pour toi : rien n'est envoyé aux participants.</p>
            <ul class="santa-people santa-pairs">${pairs.map(p => html`
                <li><span>${p.giver}</span><span aria-hidden="true">→</span><span>${p.receiver}</span></li>`)}</ul>
            <button class="btn btn-ghost btn-sm" data-click="hideSecretSantaPairs">Masquer</button>`;
}

export async function revealSecretSanta(serverId, btn) {
    if (!(await askConfirm('Tu vas voir qui offre un cadeau à qui. C\'est noté dans le journal du serveur.', { title: 'Révéler les attributions ?', confirm: '🔍 Révéler' }))) return;
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(serverId)}/secretsanta/reveal`, { method: 'POST' });
        const box = document.getElementById('santa-pairs');
        if (box && result?.pairs) {
            box.innerHTML = String(santaPairsHtml(result.pairs));
            box.hidden = false;
        }
    } catch (e) { /* handled by api() */ }
    finally {
        if (btn) btn.disabled = false;
    }
}

export function hideSecretSantaPairs() {
    const box = document.getElementById('santa-pairs');
    if (!box) return;
    box.innerHTML = '';
    box.hidden = true;
}

// The pairs who mustn't draw each other (P42): only for whom may change them,
// as /secretsanta paires-exclues (the API checks it again), until the draw.
export function santaExclusionsHtml(serverId, data) {
    if (!data || !Array.isArray(data.exclusions)) return '';
    const edit = !!data.can_exclude;
    const list = data.exclusions.length
        ? html`<ul class="santa-people santa-pairs">${data.exclusions.map(e => html`
                <li><span>${e.user1.name}</span><span aria-hidden="true">↔</span><span>${e.user2.name}</span>
                    ${edit && html`<button class="btn btn-ghost btn-sm" data-click="allowSecretSantaPair" data-click-args="${actionArgs(serverId, e.user1.id, e.user2.id)}" title="Les autoriser à se tirer" aria-label="Autoriser ${e.user1.name} et ${e.user2.name} à se tirer">✕</button>`}</li>`)}</ul>`
        : html`<p class="u-c-text-muted">Aucune paire exclue : chacun peut tirer n'importe qui.</p>`;
    const form = edit && html`
            <div class="santa-exclude">
                ${raw(memberPickerHtml('', 'data-santa-pair="1"'))}
                <span aria-hidden="true">↔</span>
                ${raw(memberPickerHtml('', 'data-santa-pair="2"'))}
                <button class="btn btn-secondary btn-sm" data-click="excludeSecretSantaPair" data-click-args="${actionArgs(serverId)}">🚫 Exclure la paire</button>
            </div>`;
    return String(html`
            <h4 class="u-m-16-0-8">Paires exclues</h4>
            <p class="u-c-text-muted u-fs-0_85rem">Les deux membres d'une paire ne peuvent pas se tirer l'un l'autre (un couple, des colocataires…).${edit ? '' : ' Le tirage a eu lieu : elles ne changent plus.'}</p>
            ${list}${form}`);
}

export async function loadSecretSanta(serverId) {
    const container = document.getElementById('secretsanta-session');
    if (!container) return;
    try {
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/secretsanta`);
        container.innerHTML = secretSantaHtml(serverId, data?.session || null) + santaExclusionsHtml(serverId, data);
        populateMemberPickers(container);
    } catch (e) {
        container.innerHTML = String(html`<div class="empty-state"><h3>Erreur</h3><p>${String(e.message || e)}</p></div>`);
    }
}

async function changePair(serverId, path, user1, user2) {
    try {
        await api(`/api/servers/${encodeURIComponent(serverId)}/secretsanta/${path}`, {
            method: 'POST',
            body: JSON.stringify({ user1, user2 }),
        });
        return true;
    } catch (e) {
        return false;  // handled by api()
    } finally {
        loadSecretSanta(serverId);
    }
}

export async function excludeSecretSantaPair(serverId) {
    const pick = (n) => {
        const picker = document.querySelector(`.santa-exclude .member-picker:has([data-santa-pair="${n}"])`);
        return picker ? getPickerSelectedId(picker) : '';
    };
    const [user1, user2] = [pick(1), pick(2)];
    if (!user1 || !user2) {
        toast('Choisis les deux membres de la paire.', 'warning');
        return;
    }
    if (await changePair(serverId, 'exclusions', user1, user2)) toast('Paire exclue.', 'success');
}

export async function allowSecretSantaPair(serverId, user1, user2) {
    if (await changePair(serverId, 'exclusions/remove', user1, user2)) toast('Ils peuvent à nouveau se tirer.', 'success');
}

export async function drawSecretSanta(serverId, btn) {
    if (!(await askConfirm('Chaque participant reçoit son destinataire par MP, et les inscriptions sont closes.', { title: 'Lancer le tirage ?', confirm: '🎲 Lancer le tirage' }))) return;
    const allow = !!document.getElementById('santa-subgroups')?.checked;
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(serverId)}/secretsanta/draw`, {
            method: 'POST',
            body: JSON.stringify({ allow_subgroups: allow }),
        });
        toast(`Tirage effectué pour ${result.count} participants.`, 'success');
        if (result.subgroups > 1) toast(`${result.subgroups} sous-groupes ont été formés.`, 'info');
        if ((result.failed_dms || []).length) {
            toast(`MP impossible pour : ${result.failed_dms.join(', ')}. Ils peuvent utiliser /secretsanta relancer une fois leurs MP ouverts.`, 'warning');
        }
    } catch (e) { /* handled by api() */ }
    finally {
        if (btn) btn.disabled = false;
        loadSecretSanta(serverId);
    }
}

registerActions({
    allowSecretSantaPair,
    drawSecretSanta,
    excludeSecretSantaPair,
    hideSecretSantaPairs,
    loadSecretSanta,
    revealSecretSanta,
});
