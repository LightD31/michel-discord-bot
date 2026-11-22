// The role picker.
import { html, raw } from '../../core/html.js';
import { registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { state } from '../../core/state.js';

// ── Role picker (P3) ──────────────────────────────
// Roles are picked by name, with their Discord colour. /role-choices says,
// for each role and each use a save checks (``ui(role_use=…)``), why the
// editor may not pick it: those roles carry a 🔒, and the reason shows as
// soon as one is selected, before the save refuses it. The manual input
// holds the value (the one collected), the list writes into it.
export const _roleChoiceCache = {};
export const ROLE_COLOR = /^#[0-9a-f]{6}$/i;

export async function fetchRoleChoices(serverId) {
    if (!serverId) return null;
    if (_roleChoiceCache[serverId]) return _roleChoiceCache[serverId];
    try {
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/role-choices`);
        if (data && Array.isArray(data.roles)) {
            _roleChoiceCache[serverId] = data.roles;
            return data.roles;
        }
    } catch (e) {
        console.warn('Could not fetch roles', e);
    }
    return null;
}

export function knownRole(id) {
    return (_roleChoiceCache[state.currentServerId] || []).find(r => r.id === String(id)) || null;
}

// "<@&123>" in a refusal names the role the way Discord would.
export function withRoleNames(text) {
    return String(text || '').replace(/<@&(\d+)>/g, (m, id) => {
        const role = knownRole(id);
        return role ? `« ${role.name} »` : 'ce rôle';
    });
}

export function roleRefusal(role, use) {
    return (role && use && role.refusals) ? withRoleNames(role.refusals[use] || '') : '';
}

export function paintDot(dot, color) {
    if (dot) dot.style.background = color && ROLE_COLOR.test(color) ? color : '';
}

export function rolePickerHtml(currentId, fieldAttr, use, selectId = '') {
    const id = currentId || '';
    return String(html`
            <div class="role-picker" data-role-use="${use || ''}">
                <div class="role-picker-row">
                    <span class="role-dot" aria-hidden="true"></span>
                    <select class="form-input role-picker-select" ${raw(selectId ? `id="${selectId}"` : 'aria-label="Choisir un rôle"')}
                            data-change="onRolePickerChange" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                        <option value="">— Chargement des rôles… —</option>
                        ${id && html`<option value="${id}" selected>${id}</option>`}
                    </select>
                    <input type="text" class="form-input role-picker-manual" ${raw(fieldAttr)} value="${id}" placeholder="ID du rôle" hidden>
                    <button type="button" class="btn-channel-toggle" data-click="toggleRolePickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                </div>
                <div class="picker-warning" role="status"></div>
            </div>`);
}

export function roleOptionsHtml(roles, use, exclude) {
    let opts = '';
    for (const r of roles) {
        if (exclude && exclude.has(r.id)) continue;
        const locked = roleRefusal(r, use) ? ' 🔒' : '';
        opts += String(html`<option value="${r.id}">${r.name}${raw(locked)}</option>`);
    }
    return opts;
}

export function fillRolePicker(picker, roles) {
    const select = picker.querySelector('.role-picker-select');
    const manual = picker.querySelector('.role-picker-manual');
    if (!roles) {
        toggleRolePickerMode(picker.querySelector('.btn-channel-toggle'), true);
        select.innerHTML = '<option value="">— Bot hors ligne —</option>';
        return;
    }
    const current = manual.value.trim();
    select.innerHTML = '<option value="">— Aucun rôle —</option>' + roleOptionsHtml(roles, picker.dataset.roleUse);
    if (current && !knownRole(current)) select.add(new Option(`Rôle introuvable (${current})`, current), 1);
    select.value = current;
    updateRolePicker(picker);
}

export function updateRolePicker(picker) {
    const id = picker.querySelector('.role-picker-manual').value.trim();
    const role = knownRole(id);
    paintDot(picker.querySelector('.role-dot'), role?.color);
    let warning = '';
    if (id && !role && _roleChoiceCache[state.currentServerId]) {
        warning = "Ce rôle n'existe plus sur le serveur.";
    } else if (role) {
        warning = roleRefusal(role, picker.dataset.roleUse);
    }
    picker.querySelector('.picker-warning').textContent = warning ? `⚠️ ${warning}` : '';
}

export function onRolePickerChange(select) {
    const picker = select.closest('.role-picker');
    if (!picker) return;
    picker.querySelector('.role-picker-manual').value = select.value;
    updateRolePicker(picker);
}

export function toggleRolePickerMode(btn, forceManual) {
    const picker = btn?.closest('.role-picker');
    if (!picker) return;
    const select = picker.querySelector('.role-picker-select');
    const manual = picker.querySelector('.role-picker-manual');
    const showManual = forceManual === true ? true : manual.hidden;
    manual.hidden = !showManual;
    select.hidden = showManual;
    btn.textContent = showManual ? '☰' : '✎';
    btn.title = showManual ? 'Choisir dans la liste' : 'Saisie manuelle';
    if (!showManual) {
        const typed = manual.value.trim();
        if (typed && !select.querySelector(`option[value="${CSS.escape(typed)}"]`)) {
            select.add(new Option(`Rôle introuvable (${typed})`, typed), 1);
        }
        select.value = typed;
        updateRolePicker(picker);
    }
}

registerActions({
    onRolePickerChange,
    toggleRolePickerMode,
});
