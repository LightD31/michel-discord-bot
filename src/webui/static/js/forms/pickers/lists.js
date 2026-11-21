// Channel and role lists (chips).
import { registerActions } from '../../core/actions.js';
import { DIRTY_SCOPE, setDirty } from '../../core/dirty.js';
import { state } from '../../core/state.js';
import { escapeHtml } from '../../core/util.js';
import { channelOptionsHtml, fetchServerChannels } from './channels.js';
import { ROLE_COLOR, fetchRoleChoices, fillRolePicker, knownRole, roleOptionsHtml, roleRefusal } from './roles.js';

// ── Channel and role lists (P3) ───────────────────
// A ``channellist`` / ``rolelist`` field: one chip per channel or role,
// named, and a list to add one more. Without the bot, ids are typed.
// A null *key* makes a picker inside a list entry, which its widget reads
// (not a form value of its own).
export function multiPickerHtml(key, type, ids, use) {
    const kind = type === 'rolelist' ? 'role' : 'channel';
    const chips = ids.map(id => chipHtml(String(id), String(id), '')).join('');
    return `
            <div class="multi-picker" ${key == null ? '' : `data-field="${escapeHtml(key)}"`} data-type="${type}" data-kind="${kind}" data-role-use="${escapeHtml(use || '')}">
                <div class="multi-picker-chips">${chips}</div>
                <select class="form-input multi-picker-add" aria-label="${kind === 'role' ? 'Ajouter un rôle' : 'Ajouter un salon'}"
                        data-change="addMultiPickerChoice" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <option value="">— Chargement… —</option>
                </select>
                <input type="text" class="form-input multi-picker-manual" style="display:none;"
                       placeholder="ID ${kind === 'role' ? 'du rôle' : 'du salon'}, puis Entrée"
                       data-keydown="onMultiPickerManualKey" data-keydown-args="[{&quot;$&quot;:&quot;el&quot;},{&quot;$&quot;:&quot;event&quot;}]">
                <div class="picker-warning" role="status"></div>
            </div>`;
}

export function chipHtml(id, label, color, extra = '') {
    const dot = ROLE_COLOR.test(color || '') ? `<span class="role-dot" style="background:${color}" aria-hidden="true"></span>` : '';
    return `<span class="chip ${extra}" data-id="${escapeHtml(id)}">${dot}<span class="chip-label">${escapeHtml(label)}</span>`
        + `<button type="button" class="chip-remove" data-click="removeMultiPickerChip" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" aria-label="Retirer ${escapeHtml(label)}" title="Retirer">✕</button></span>`;
}

export function multiPickerIds(picker) {
    return [...picker.querySelectorAll('.chip')].map(c => c.dataset.id);
}

export async function populateMultiPickers(container) {
    const serverId = state.currentServerId;
    if (!serverId || !container) return;
    const pickers = container.querySelectorAll('.multi-picker');
    if (!pickers.length) return;
    const needs = new Set([...pickers].map(p => p.dataset.kind));
    const [roles, channels] = await Promise.all([
        needs.has('role') ? fetchRoleChoices(serverId) : null,
        needs.has('channel') ? fetchServerChannels(serverId) : null,
    ]);
    for (const picker of pickers) {
        picker._source = picker.dataset.kind === 'role' ? roles : channels;
        refreshMultiPicker(picker);
    }
}

export function refreshMultiPicker(picker) {
    const source = picker._source;
    const select = picker.querySelector('.multi-picker-add');
    const manual = picker.querySelector('.multi-picker-manual');
    const isRole = picker.dataset.kind === 'role';
    const ids = multiPickerIds(picker);
    if (!source) {
        select.style.display = 'none';
        manual.style.display = '';
        return;
    }
    const use = picker.dataset.roleUse;
    const channelsById = isRole ? null : new Map(source.channels.map(c => [String(c.id), c]));
    const warnings = [];
    picker.querySelector('.multi-picker-chips').innerHTML = ids.map(id => {
        if (isRole) {
            const role = knownRole(id);
            if (!role) {
                warnings.push(`Rôle introuvable (${id}).`);
                return chipHtml(id, `Rôle introuvable (${id})`, '', 'chip-unknown');
            }
            const refusal = roleRefusal(role, use);
            if (refusal) warnings.push(refusal);
            return chipHtml(id, role.name, role.color, refusal ? 'chip-warning' : '');
        }
        const channel = channelsById.get(id);
        if (!channel) {
            warnings.push(`Salon introuvable (${id}).`);
            return chipHtml(id, `Salon introuvable (${id})`, '', 'chip-unknown');
        }
        return chipHtml(id, `${channel.is_thread ? '🧵 ' : '#'}${channel.name}`, '');
    }).join('');
    const taken = new Set(ids);
    select.innerHTML = `<option value="">${isRole ? '+ Ajouter un rôle…' : '+ Ajouter un salon…'}</option>`
        + (isRole ? roleOptionsHtml(source, use, taken) : channelOptionsHtml(source, taken));
    picker.querySelector('.picker-warning').textContent = warnings.length ? `⚠️ ${warnings.join(' ')}` : '';
}

export function addMultiPickerId(picker, id) {
    id = String(id || '').trim();
    if (!id || multiPickerIds(picker).includes(id)) return;
    picker.querySelector('.multi-picker-chips').insertAdjacentHTML('beforeend', chipHtml(id, id, ''));
    refreshMultiPicker(picker);
    if (picker.closest(DIRTY_SCOPE)) setDirty(true);
}

export function addMultiPickerChoice(select) {
    const picker = select.closest('.multi-picker');
    if (picker && select.value) addMultiPickerId(picker, select.value);
}

export function onMultiPickerManualKey(input, event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const picker = input.closest('.multi-picker');
    if (!picker) return;
    addMultiPickerId(picker, input.value);
    input.value = '';
}

export function removeMultiPickerChip(btn) {
    const picker = btn.closest('.multi-picker');
    btn.closest('.chip')?.remove();
    if (!picker) return;
    refreshMultiPicker(picker);
    if (picker.closest(DIRTY_SCOPE)) setDirty(true);
    picker.querySelector('.multi-picker-add')?.focus();
}

export async function populateRolePickers(container) {
    const serverId = state.currentServerId;
    if (!serverId || !container) return;
    const pickers = container.querySelectorAll('.role-picker');
    if (!pickers.length) return;
    const roles = await fetchRoleChoices(serverId);
    for (const picker of pickers) fillRolePicker(picker, roles);
}

registerActions({
    addMultiPickerChoice,
    onMultiPickerManualKey,
    removeMultiPickerChip,
});
