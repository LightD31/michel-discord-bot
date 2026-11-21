// The infraction browser.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { escapeHtml } from '../core/util.js';
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
            if (!m) return `<span title="${escapeHtml(id)}">Ancien membre</span>`;
            const prenom = getDiscord2NameMap()[m.id];
            const name = prenom || m.display_name || m.username;
            const uname = m.username && m.username !== name ? ` <span style="color:var(--text-muted)">(${escapeHtml(m.username)})</span>` : '';
            return `${escapeHtml(name)}${uname}`;
        };
        container.innerHTML = items.map(i => {
            const label = INFRACTION_TYPE_LABELS[i.type] || i.type;
            const date = i.created_at ? new Date(i.created_at).toLocaleString('fr-FR') : '';
            const dur = i.duration_seconds ? ` · ${Math.round(i.duration_seconds / 60)} min` : '';
            const by = i.moderator_id === 'automod' ? '🛡️ Automod' : who(i.moderator_id);
            const revokeBtn = i.active
                ? `<button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="revokeInfraction" data-click-args="${actionArgs(serverId, i.id)}">Révoquer</button>`
                : `<span style="color:var(--text-muted); font-size:12px;">Révoqué</span>`;
            return `
                    <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; ${i.active ? '' : 'opacity:0.55;'} display:flex; flex-direction:column; gap:6px;">
                        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                            <div>
                                <strong>#${i.id} · ${escapeHtml(label)}</strong>${dur}
                                <a href="#" class="inf-member" style="margin-left:8px;" title="Voir l'historique de ce membre"
                                   data-click="filterInfractionsByUser" data-click-args="${actionArgs(serverId, String(i.user_id))}" data-prevent>${who(i.user_id)}</a>
                            </div>
                            <div style="display:flex; gap:6px; align-items:center;">${revokeBtn}</div>
                        </div>
                        <div style="font-size:13px;">${escapeHtml(i.reason || 'Aucune raison')}</div>
                        <div style="font-size:12px; color:var(--text-muted);">Par ${by} · ${escapeHtml(date)}</div>
                    </div>
                `;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e))}</p></div>`;
    }
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
    revokeInfraction,
});
