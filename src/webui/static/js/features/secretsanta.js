// Secret Santa: the session and its draw.
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { escapeHtml } from '../core/util.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Secret Santa: the session and its draw (P2) ─────────────────────
// Counts only once drawn: who sent or received a parcel would hint at pairs.
export function santaProgress(label, done, total) {
    const pct = total ? Math.round(100 * done / total) : 0;
    return `
            <div class="santa-progress">
                <div class="santa-progress-label"><span>${label}</span><strong>${done}/${total}</strong></div>
                <div class="santa-progress-bar" role="progressbar" aria-label="${escapeHtml(label)}" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${done}"><span style="width:${pct}%"></span></div>
            </div>`;
}

export function secretSantaHtml(serverId, s) {
    if (!s) {
        return `<div class="empty-state"><h3>Aucune session</h3><p>Crées-en une sur Discord avec <code>/secretsanta creer</code>, en présentiel ou par colis (option <code>colis</code>).</p></div>`;
    }
    const withList = s.participants.filter(p => p.has_wishlist).length;
    const facts = [
        ['Statut', s.is_drawn ? '✅ Tirage effectué' : '⏳ Inscriptions ouvertes'],
        ['Échange', s.exchange_label ? escapeHtml(s.exchange_label) : '<span style="color:var(--text-muted)">Pas de date</span>'],
        ['Budget', s.budget ? escapeHtml(s.budget) : '<span style="color:var(--text-muted)">Non précisé</span>'],
        ['Remise', s.shipping ? '📦 Par colis (suivi et rappel)' : '🤝 En main propre'],
        ['Organisé par', escapeHtml(s.created_by)],
    ];
    const people = s.participants.length
        ? `<ul class="santa-people">${s.participants.map(p => `
                <li><span>${escapeHtml(p.name)}</span>${p.has_wishlist ? '<span class="santa-list" title="A laissé une liste d\'envies">📝 liste</span>' : ''}</li>`).join('')}</ul>`
        : '<p style="color:var(--text-muted)">Personne n\'est encore inscrit.</p>';
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
            <h4 style="margin:16px 0 8px;">Participants (${s.total})</h4>
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

export async function loadSecretSanta(serverId) {
    const container = document.getElementById('secretsanta-session');
    if (!container) return;
    try {
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/secretsanta`);
        container.innerHTML = secretSantaHtml(serverId, data?.session || null);
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e.message || e))}</p></div>`;
    }
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
    drawSecretSanta,
    hideSecretSantaPairs,
    loadSecretSanta,
    revealSecretSanta,
});
