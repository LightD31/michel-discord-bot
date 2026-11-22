// The role menu builder.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { html } from '../core/html.js';
import { discordButtons, discordEmbed, discordMessage, discordSelect } from '../ui/discord.js';
import { confirmDiscard, setDirty } from '../core/dirty.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { fetchServerChannels } from '../forms/pickers/channels.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';

// ── Reaction Roles builder ────────────────────────
export const _rolesCache = {}; // serverId -> [{id, name, color, position}]
export let _rolemenuEditing = null;  // null = create, otherwise menu id being edited

export async function fetchServerRoles(serverId) {
    if (!serverId) return [];
    if (_rolesCache[serverId]) return _rolesCache[serverId];
    try {
        const data = await api(`/api/servers/${serverId}/roles`);
        const roles = data?.roles || [];
        _rolesCache[serverId] = roles;
        return roles;
    } catch (e) {
        console.warn('Could not fetch roles', e);
        return [];
    }
}

export async function loadRoleMenus(serverId) {
    const container = document.getElementById('rolemenu-list');
    if (!container) return;
    try {
        const data = await api(`/api/servers/${serverId}/rolemenus`);
        const menus = data?.menus || [];
        if (menus.length === 0) {
            container.innerHTML = `<div class="empty-state"><h3>Aucun menu</h3><p>Clique sur « Nouveau menu » pour en créer un.</p></div>`;
            return;
        }
        container.innerHTML = menus.map(m => {
            const jumpUrl = m.message_id
                ? `https://discord.com/channels/${m.guild_id}/${m.channel_id}/${m.message_id}`
                : null;
            const entriesPreview = (m.entries || []).slice(0, 5).map(e =>
                `<span style="margin-right:8px;">${escapeHtml(e.emoji)} ${escapeHtml(e.label)}</span>`
            ).join('');
            const more = (m.entries?.length || 0) > 5 ? ` <span style="color:var(--text-muted)">+${m.entries.length - 5}</span>` : '';
            return `
                    <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:6px;">
                        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                            <div>
                                <strong>${escapeHtml(m.title || '(sans titre)')}</strong>
                                <span style="color:var(--text-muted); margin-left:8px;">&lt;#${escapeHtml(m.channel_id)}&gt;</span>
                                ${jumpUrl ? `<a href="${jumpUrl}" target="_blank" style="margin-left:8px;">↗ Discord</a>` : ''}
                            </div>
                            <div style="display:flex; gap:6px;">
                                <button class="btn btn-ghost btn-sm" data-click="openRoleMenuBuilder" data-click-args="${actionArgs(serverId, m.id)}">✏️ Modifier</button>
                                <button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="deleteRoleMenu" data-click-args="${actionArgs(serverId, m.id)}">🗑️ Supprimer</button>
                            </div>
                        </div>
                        <div style="font-size:13px;">${entriesPreview}${more}</div>
                    </div>
                `;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e))}</p></div>`;
    }
}

export function _renderRoleSelect(roles, currentRoleId) {
    let opts = '<option value="">— Choisir un rôle —</option>';
    for (const r of roles) {
        const sel = String(r.id) === String(currentRoleId) ? 'selected' : '';
        opts += `<option value="${escapeHtml(r.id)}" ${sel}>@${escapeHtml(r.name)}</option>`;
    }
    return opts;
}

export function _renderEntryRow(roles, entry) {
    // A new menu starts with an empty entry ({}), and a stored one may lack
    // its emoji: each missing field is an empty input, never "undefined".
    const e = { role_id: '', emoji: '', label: '', ...(entry || {}) };
    for (const k of ['role_id', 'emoji', 'label']) e[k] = e[k] ?? '';
    return `
            <div class="rolemenu-entry" style="display:grid; grid-template-columns: 1fr 80px 1fr 36px; gap:8px; align-items:center; margin-bottom:6px;">
                <select class="form-input" data-rrf="role_id">${_renderRoleSelect(roles, e.role_id)}</select>
                <input type="text" class="form-input" data-rrf="emoji" value="${escapeHtml(e.emoji)}" placeholder="🎮" maxlength="64">
                <input type="text" class="form-input" data-rrf="label" value="${escapeHtml(e.label)}" placeholder="Libellé du bouton" maxlength="80">
                <button type="button" class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.rolemenu-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>
        `;
}

export async function openRoleMenuBuilder(serverId, menuId) {
    const card = document.getElementById('rolemenu-builder-card');
    const body = document.getElementById('rolemenu-builder-body');
    const titleEl = document.getElementById('rolemenu-builder-title');
    if (!card || !body) return;
    _rolemenuEditing = menuId || null;
    titleEl.textContent = menuId ? 'Modifier le menu' : 'Nouveau menu';

    body.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    card.style.display = 'block';
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const [roles, channelData, menus] = await Promise.all([
        fetchServerRoles(serverId),
        fetchServerChannels(serverId),
        menuId ? api(`/api/servers/${serverId}/rolemenus`) : Promise.resolve(null),
    ]);

    let menu = null;
    if (menuId && menus?.menus) {
        menu = menus.menus.find(m => m.id === menuId);
    }
    const initial = menu || { channel_id: '', title: '', description: '', entries: [{}] };
    if (!initial.entries || initial.entries.length === 0) initial.entries = [{}];

    let channelOpts = '<option value="">— Choisir un salon —</option>';
    if (channelData?.channels) {
        const grouped = new Map();
        for (const c of channelData.channels) {
            if (c.is_thread || c.is_forum) continue;  // a menu is a message: not in a forum
            const catId = c.parent_id || '__none__';
            if (!grouped.has(catId)) grouped.set(catId, []);
            grouped.get(catId).push(c);
        }
        const orderedCats = [
            ['__none__', 'Sans catégorie'],
            ...((channelData.categories || []).map(c => [c.id, c.name])),
        ];
        for (const [catId, catName] of orderedCats) {
            const list = grouped.get(catId);
            if (!list || list.length === 0) continue;
            channelOpts += `<optgroup label="${escapeHtml(catName)}">`;
            for (const ch of list) {
                const sel = String(ch.id) === String(initial.channel_id) ? 'selected' : '';
                channelOpts += `<option value="${escapeHtml(ch.id)}" ${sel}>#${escapeHtml(ch.name)}</option>`;
            }
            channelOpts += `</optgroup>`;
        }
    }

    body.innerHTML = `
            <div class="form-grid" style="display:flex; flex-direction:column; gap:12px;">
                <div>
                    <label class="form-label">Salon</label>
                    <select class="form-input" id="rrf-channel" ${menuId ? 'disabled title="Le salon est figé une fois le menu créé."' : ''}>
                        ${channelOpts}
                    </select>
                </div>
                <div>
                    <label class="form-label">Titre</label>
                    <input type="text" class="form-input" id="rrf-title" value="${escapeHtml(initial.title || '')}" maxlength="256" placeholder="Ex : Choisis tes rôles">
                </div>
                <div>
                    <label class="form-label">Description (optionnel)</label>
                    <textarea class="form-textarea" id="rrf-description" rows="2" placeholder="Affichée au-dessus de la liste des rôles">${escapeHtml(initial.description || '')}</textarea>
                </div>
                <div class="rolemenu-style-row">
                    <div>
                        <label class="form-label" for="rrf-style">Présentation</label>
                        <select class="form-input" id="rrf-style">
                            <option value="buttons" ${initial.style !== 'select' ? 'selected' : ''}>Un bouton par rôle</option>
                            <option value="select" ${initial.style === 'select' ? 'selected' : ''}>Une liste déroulante</option>
                        </select>
                    </div>
                    <div>
                        <label class="form-label" for="rrf-max">Rôles par membre</label>
                        <input type="number" class="form-input" id="rrf-max" min="1" max="25" placeholder="Sans limite"
                            value="${Number.isInteger(initial.max_roles) ? initial.max_roles : ''}">
                        <div class="form-description">Vide : autant qu'il veut. 1 : un seul, le nouveau remplace l'ancien (couleurs, équipes).</div>
                    </div>
                </div>
                <div>
                    <label class="form-label">Entrées (max 25)</label>
                    <div id="rrf-entries">
                        ${initial.entries.map(e => _renderEntryRow(roles, e)).join('')}
                    </div>
                    <button type="button" class="btn-add-entry" data-click="addRoleMenuEntry">+ Ajouter une entrée</button>
                </div>
                <div>
                    <div class="form-label">Aperçu</div>
                    <div id="rrf-preview" class="discord-preview" aria-live="polite" aria-label="Aperçu dans Discord"></div>
                </div>
            </div>
        `;
    refreshRoleMenuPreview();
}

// ── Preview (P16): the menu as src/discord_ext/rolemenus.py renders it ──
export function roleMenuPreviewHtml(payload, roleNames) {
    const lines = payload.entries
        .filter(e => e.role_id || e.label)
        .map(e => [e.emoji, e.role_id ? `<@&${e.role_id}>` : ''].filter(Boolean).join(' ') + ` — ${e.label}`);
    let description = (payload.description ? payload.description + '\n\n' : '') + lines.join('\n');
    if (payload.max_roles === 1) description += '\n\n*Un seul rôle à la fois : en choisir un retire l\'autre.*';
    else if (payload.max_roles > 1) description += `\n\n*${payload.max_roles} rôles au plus à la fois.*`;
    const embed = discordEmbed({
        title: payload.title || 'Sans titre',
        description,
        author: '🎭 Rôles à boutons',
        footer: 'Michel · Rôles à boutons',
    }, { roles: roleNames });
    const controls = payload.style === 'select'
        ? html`${discordSelect('Choisis tes rôles')}${discordButtons([{ label: 'Retirer mes rôles' }])}`
        : discordButtons(payload.entries.filter(e => e.label).map(e => ({ emoji: e.emoji, label: e.label })));
    return discordMessage(html`${embed}${controls}`);
}

export function refreshRoleMenuPreview() {
    const box = document.getElementById('rrf-preview');
    if (!box) return;
    const roles = _rolesCache[state.currentServerId] || [];
    const names = new Map(roles.map(r => [String(r.id), r.name]));
    box.innerHTML = String(roleMenuPreviewHtml(_collectRoleMenuPayload(), names));
}

for (const type of ['input', 'change', 'click']) {
    document.addEventListener(type, (event) => {
        if (event.target instanceof Element && event.target.closest('#rolemenu-builder-body')) {
            refreshRoleMenuPreview();
        }
    });
}

export function addRoleMenuEntry() {
    const list = document.getElementById('rrf-entries');
    if (!list) return;
    if (list.querySelectorAll('.rolemenu-entry').length >= 25) {
        toast('Maximum 25 entrées par menu (limite Discord).', 'warning');
        return;
    }
    const sid = state.currentServerId;
    const roles = _rolesCache[sid] || [];
    list.insertAdjacentHTML('beforeend', _renderEntryRow(roles, null));
}

export async function closeRoleMenuBuilder() {
    if (!(await confirmDiscard())) return;
    const card = document.getElementById('rolemenu-builder-card');
    if (card) card.style.display = 'none';
    _rolemenuEditing = null;
}

export function _collectRoleMenuPayload() {
    const channel_id = document.getElementById('rrf-channel')?.value?.trim() || '';
    const title = document.getElementById('rrf-title')?.value?.trim() || '';
    const description = document.getElementById('rrf-description')?.value?.trim() || '';
    const entries = [];
    document.querySelectorAll('#rrf-entries .rolemenu-entry').forEach(row => {
        const role_id = row.querySelector('[data-rrf="role_id"]')?.value?.trim() || '';
        const emoji = row.querySelector('[data-rrf="emoji"]')?.value?.trim() || '';
        const label = row.querySelector('[data-rrf="label"]')?.value?.trim() || '';
        if (role_id || emoji || label) entries.push({ role_id, emoji, label });
    });
    const style = document.getElementById('rrf-style')?.value === 'select' ? 'select' : 'buttons';
    const max = Number.parseInt(document.getElementById('rrf-max')?.value || '', 10);
    // Empty is no limit: null on a new menu, 0 to remove the limit of an existing one.
    const max_roles = Number.isInteger(max) && max >= 1 ? Math.min(max, 25) : (_rolemenuEditing ? 0 : null);
    return { channel_id, title, description: description || null, entries, style, max_roles };
}

export async function saveRoleMenu() {
    const sid = state.currentServerId;
    if (!sid) return;
    const payload = _collectRoleMenuPayload();
    if (!_rolemenuEditing && !payload.channel_id) return toast('Salon requis.', 'warning');
    if (!payload.title) return toast('Titre requis.', 'warning');
    if (!payload.entries.length) return toast('Au moins une entrée requise.', 'warning');
    for (const [i, e] of payload.entries.entries()) {
        if (!e.role_id) return toast(`Entrée ${i + 1} : rôle manquant.`, 'warning');
        if (!e.emoji) return toast(`Entrée ${i + 1} : emoji manquant.`, 'warning');
        if (!e.label) return toast(`Entrée ${i + 1} : libellé manquant.`, 'warning');
    }

    try {
        if (_rolemenuEditing) {
            const body = {
                title: payload.title,
                description: payload.description,
                entries: payload.entries,
                style: payload.style,
                max_roles: payload.max_roles,
            };
            await api(`/api/servers/${sid}/rolemenus/${_rolemenuEditing}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
            toast('Menu mis à jour.', 'success');
        } else {
            await api(`/api/servers/${sid}/rolemenus`, {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            toast('Menu créé.', 'success');
        }
        setDirty(false);
        closeRoleMenuBuilder();
        loadRoleMenus(sid);
    } catch (e) { /* handled by api() */ }
}

export async function deleteRoleMenu(serverId, menuId) {
    if (!(await askConfirm('Supprimer ce menu ? Le message Discord associé sera également supprimé.', { title: 'Supprimer le menu', confirm: 'Supprimer', danger: true }))) return;
    try {
        await api(`/api/servers/${serverId}/rolemenus/${menuId}`, { method: 'DELETE' });
        toast('Menu supprimé.', 'success');
        if (_rolemenuEditing === menuId) closeRoleMenuBuilder();
        loadRoleMenus(serverId);
    } catch (e) { /* handled by api() */ }
}

registerActions({
    addRoleMenuEntry,
    closeRoleMenuBuilder,
    deleteRoleMenu,
    openRoleMenuBuilder,
    saveRoleMenu,
});
