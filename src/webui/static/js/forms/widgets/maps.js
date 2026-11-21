// Name maps and key-value maps.
import { registerActions } from '../../core/actions.js';
import { escapeHtml } from '../../core/util.js';
import { populateRolePickers } from '../pickers/lists.js';
import { memberPickerHtml, populateMemberPickers, refreshNameMapListOptions } from '../pickers/members.js';
import { rolePickerHtml } from '../pickers/roles.js';

export function addNameMapEntry(btn) {
    const list = btn.previousElementSibling;
    const entry = document.createElement('div');
    entry.className = 'namemap-entry';
    entry.innerHTML = `
            ${memberPickerHtml('', 'data-namemap-field="discord_id"')}
            <input type="text" data-namemap-field="name" value="" placeholder="ex: Jean">
            <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
        `;
    list.appendChild(entry);
    // Populate (fetches if not cached) then refresh the whole list so the new
    // picker excludes already-used IDs and so do siblings
    populateMemberPickers(entry).then(() => refreshNameMapListOptions(list));
    const search = entry.querySelector('.member-picker-search');
    if (search) search.focus();
}

// One row of a ``keyvaluemap``: a role picker on the side that names a
// role (``role_use``, the keys with ``role_keys``), a member picker for
// keys that are members (``member_keys``), a text input otherwise.
export function keyValueEntryHtml(roleUse, roleKeys, memberKeys, keyLabel, valueLabel, mapKey, mapValue) {
    const text = (side, label, val) => `<input type="text" data-kvmap-field="${side}" value="${escapeHtml(val)}" placeholder="ex: ${escapeHtml(label).toLowerCase()}">`;
    let keyCell = text('key', keyLabel, mapKey);
    let valueCell = text('value', valueLabel, mapValue);
    if (memberKeys) keyCell = memberPickerHtml(mapKey, 'data-kvmap-field="key"');
    if (roleUse && roleKeys) keyCell = rolePickerHtml(mapKey, 'data-kvmap-field="key"', roleUse);
    if (roleUse && !roleKeys) valueCell = rolePickerHtml(mapValue, 'data-kvmap-field="value"', roleUse);
    return `
            <div class="keyvaluemap-entry">
                ${keyCell}
                ${valueCell}
                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.keyvaluemap-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>`;
}

export function addKeyValueMapEntry(btn) {
    const list = btn.previousElementSibling;
    const d = list.dataset;
    list.insertAdjacentHTML('beforeend', keyValueEntryHtml(d.roleUse || '', !!d.roleKeys, !!d.memberKeys,
        d.keyLabel || 'Clé', d.valueLabel || 'Valeur', '', ''));
    const entry = list.lastElementChild;
    populateRolePickers(entry);
    populateMemberPickers(entry);
    entry.querySelector('input:not([style*="none"]), select')?.focus();
}

registerActions({
    addKeyValueMapEntry,
    addNameMapEntry,
});
