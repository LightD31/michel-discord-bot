// The infraction browser.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html, raw } from '../core/html.js';
import { getDiscord2NameMap, memberOptionLabel, memberStore, resolveServerMembers } from '../forms/pickers/members.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Moderation: infraction browser ──────────────────────────────────
export const INFRACTION_TYPE_LABELS = {
    warn: '⚠️ Avertissement', timeout: '🔇 Timeout', untimeout: '🔊 Fin de timeout',
    kick: '👢 Expulsion', ban: '🔨 Bannissement', unban: '♻️ Débannissement',
    note: '📝 Note', automod: '🛡️ Automod',
};

export async function loadInfractions(serverId) {
    const container = document.getElementById('infractions-list');
    if (!container) return;
    container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const userId = (document.getElementById('inf-filter-user')?.value || '').trim();
    const type = document.getElementById('inf-filter-type')?.value || '';
    const active = document.getElementById('inf-filter-active')?.value || '';
    const params = new URLSearchParams();
    if (userId) params.set('user_id', userId);
    if (type) params.set('type', type);
    if (active) params.set('active', active);
    try {
        const data = await api(`/api/servers/${serverId}/infractions?${params.toString()}`);
        const items = data?.infractions || [];
        if (items.length === 0) {
            container.innerHTML = `<div class="empty-state"><h3>Aucune infraction</h3><p>Aucune sanction ne correspond aux filtres.</p></div>`;
            return;
        }
        // Names, not ids: the members behind the cases, looked up by id.
        let members = new Map();
        try {
            members = await resolveServerMembers(serverId, items.flatMap(i => [i.user_id, i.moderator_id]));
        } catch (e) {
            console.warn('Could not resolve members', e);
        }
        const who = (id) => {
            const m = members.get(String(id));
            if (!m) return String(html`<span title="${id}">Ancien membre</span>`);
            const prenom = getDiscord2NameMap()[m.id];
            const name = prenom || m.display_name || m.username;
            const uname = m.username && m.username !== name ? String(html` <span class="u-c-text-muted">(${m.username})</span>`) : '';
            return String(html`${name}${raw(uname)}`);
        };
        container.innerHTML = items.map(i => {
            const label = INFRACTION_TYPE_LABELS[i.type] || i.type;
            const date = i.created_at ? new Date(i.created_at).toLocaleString('fr-FR') : '';
            const dur = i.duration_seconds ? ` · ${Math.round(i.duration_seconds / 60)} min` : '';
            const by = i.moderator_id === 'automod' ? '🛡️ Automod' : who(i.moderator_id);
            const revokeBtn = i.active
                ? `<button class="u-c-danger btn btn-ghost btn-sm" data-click="revokeInfraction" data-click-args="${actionArgs(serverId, i.id)}">Révoquer</button>`
                : `<span class="u-c-text-muted u-fs-12">Révoqué</span>`;
            return String(html`
                    <div class="inf-row u-bd-1-solid-border u-br-radius u-p-12 u-mb-8 u-d-flex u-fd-column u-gap-6" ${raw(i.active ? '' : 'data-revoked')}>
                        <div class="u-d-flex u-ai-center u-jc-space-between u-gap-8 u-wrap-wrap">
                            <div>
                                <strong>#${raw(i.id)} · ${label}</strong>${raw(dur)}
                                <a href="#" class="u-ml-8 inf-member" title="Voir l'historique de ce membre"
                                   data-click="filterInfractionsByUser" data-click-args="${actionArgs(serverId, String(i.user_id))}" data-prevent>${raw(who(i.user_id))}</a>
                            </div>
                            <div class="u-d-flex u-gap-6 u-ai-center">${raw(revokeBtn)}</div>
                        </div>
                        <div class="u-fs-13">${i.reason || 'Aucune raison'}</div>
                        <div class="u-fs-12 u-c-text-muted">Par ${raw(by)} · ${date}</div>
                    </div>
                `);
        }).join('');
    } catch (e) {
        container.innerHTML = String(html`<div class="empty-state"><h3>Erreur</h3><p>${String(e)}</p></div>`);
    }
}

// ── Moderation: the reported messages (P15) ─────────────────────────
export const REPORT_STATUS_LABELS = {
    open: '🚩 À traiter', deleted: '🗑️ Message supprimé', warned: '⚠️ Averti',
    timed_out: '🔇 Exclu', dismissed: '✅ Classé sans suite',
};

export async function loadReports(serverId) {
    const container = document.getElementById('reports-list');
    if (!container) return;
    container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const status = document.getElementById('report-filter')?.value ?? 'open';
    let data;
    try {
        data = await api(`/api/servers/${serverId}/reports?status=${encodeURIComponent(status)}`, { quiet: true });
    } catch (e) {
        container.innerHTML = String(html`<div class="empty-state"><p>${e.message || String(e)}</p></div>`);
        return;
    }
    if (data === null) {
        container.innerHTML = String(html`<div class="empty-state"><p>🔒 Les signalements sont réservés aux modérateurs : il faut la permission « Exclure temporairement des membres ».</p></div>`);
        return;
    }
    const items = data?.reports || [];
    if (items.length === 0) {
        container.innerHTML = String(html`<div class="empty-state"><h3>Aucun signalement</h3><p>${status === 'open' ? 'Rien à traiter pour l\'instant.' : 'Aucun message n\'a été signalé.'}</p></div>`);
        return;
    }
    let members = new Map();
    try {
        members = await resolveServerMembers(serverId, items.flatMap(r => [r.author_id, r.handled_by, ...r.reporters.map(x => x.user_id)].filter(Boolean)));
    } catch (e) {
        console.warn('Could not resolve members', e);
    }
    const name = (id) => {
        const m = members.get(String(id));
        return m ? (getDiscord2NameMap()[m.id] || m.display_name || m.username) : 'Ancien membre';
    };
    container.innerHTML = String(html`${items.map(r => html`
            <div class="report-item${r.status === 'open' ? '' : ' report-done'}">
                <div class="report-head">
                    <strong>Message de ${name(r.author_id)}</strong>
                    <span class="report-status">${REPORT_STATUS_LABELS[r.status] || r.status}</span>
                </div>
                <ul class="report-reasons">${r.reporters.map(x => html`
                    <li><span class="report-who">${name(x.user_id)}</span> : ${x.reason || 'sans raison'}</li>`)}</ul>
                <div class="report-meta">
                    ${new Date(r.created_at).toLocaleString('fr-FR')}
                    ${r.handled_by && html` · traité par ${name(r.handled_by)}`}
                    ${r.message_url && html` · <a href="${r.message_url}" target="_blank" rel="noopener">Voir le message</a>`}
                    ${r.card_url && html` · <a href="${r.card_url}" target="_blank" rel="noopener">Voir la carte</a>`}
                </div>
            </div>`)}`);
}

// A click on a member: their history, through the member filter.
export async function filterInfractionsByUser(serverId, userId) {
    const manual = document.getElementById('inf-filter-user');
    const picker = manual?.closest('.member-picker');
    if (!picker) return;
    manual.value = userId;
    picker.dataset.current = userId;
    const select = picker.querySelector('.member-picker-select');
    if (select && !select.querySelector(`option[value="${CSS.escape(userId)}"]`)) {
        const m = memberStore(serverId).byId.get(userId);
        select.add(new Option(m ? memberOptionLabel(m) : 'Ancien membre', userId), 1);
    }
    if (select) select.value = userId;
    picker.scrollIntoView({ block: 'nearest' });
    await loadInfractions(serverId);
}

export async function revokeInfraction(serverId, caseId) {
    if (!(await askConfirm(`Révoquer le cas #${caseId} ?`, { title: 'Révoquer', confirm: 'Révoquer', danger: true }))) return;
    try {
        await api(`/api/servers/${serverId}/infractions/${caseId}`, { method: 'DELETE' });
        toast(`Cas #${caseId} révoqué.`, 'success');
        loadInfractions(serverId);
    } catch (e) { /* handled by api() */ }
}

registerActions({
    filterInfractionsByUser,
    loadInfractions,
    loadReports,
    revokeInfraction,
});
