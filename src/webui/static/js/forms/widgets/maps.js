// Name maps and key-value maps.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';
import { populateRolePickers } from '../pickers/lists.js';
import { memberPickerHtml, memberPickerValue, populateMemberPickers, refreshNameMapListOptions } from '../pickers/members.js';
import { rolePickerHtml } from '../pickers/roles.js';

export function addNameMapEntry(btn) {
    const list = btn.previousElementSibling;
    const entry = document.createElement('div');
    entry.className = 'namemap-entry';
    entry.innerHTML = String(html`
            ${raw(memberPickerHtml('', 'data-namemap-field="discord_id"'))}
            <input type="text" data-namemap-field="name" value="" placeholder="ex: Jean">
            <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="${actionArgs(EL)}" title="Supprimer" aria-label="Supprimer">✕</button>`);
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
    const text = (side, label, val) => html`<input type="text" data-kvmap-field="${side}" value="${val}" placeholder="ex: ${String(label).toLowerCase()}">`;
    let keyCell = text('key', keyLabel, mapKey);
    let valueCell = text('value', valueLabel, mapValue);
    if (memberKeys) keyCell = raw(memberPickerHtml(mapKey, 'data-kvmap-field="key"'));
    if (roleUse && roleKeys) keyCell = raw(rolePickerHtml(mapKey, 'data-kvmap-field="key"', roleUse));
    if (roleUse && !roleKeys) valueCell = raw(rolePickerHtml(mapValue, 'data-kvmap-field="value"', roleUse));
    return html`
            <div class="keyvaluemap-entry">
                ${keyCell}
                ${valueCell}
                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="${actionArgs('.keyvaluemap-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>`;
}

const asMap = (value) => ((value && typeof value === 'object' && !Array.isArray(value)) ? value : {});

// discord2name: a member and the first name the bot calls them by.
export const nameMapField = {
    render: ({ key, value }) => html`
            <div class="namemap-list" data-field="${key}" data-type="discord2name">
                <div class="namemap-header"><span>Membre Discord</span><span>Prénom</span><span></span></div>
                ${Object.entries(asMap(value)).map(([discordId, name]) => html`
                <div class="namemap-entry">
                    ${raw(memberPickerHtml(discordId, 'data-namemap-field="discord_id"'))}
                    <input type="text" data-namemap-field="name" value="${String(name)}" placeholder="ex: Jean">
                    <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="${actionArgs(EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>`)}
            </div>
            <button type="button" class="btn-add-entry" data-click="addNameMapEntry" data-click-args="${actionArgs(EL)}">+ Ajouter un membre</button>`,
    collect: (list) => {
        const names = {};
        for (const entry of list.querySelectorAll('.namemap-entry')) {
            const picker = entry.querySelector('.member-picker');
            const discordId = picker
                ? memberPickerValue(picker)
                : entry.querySelector('[data-namemap-field="discord_id"]')?.value?.trim() || '';
            const name = entry.querySelector('[data-namemap-field="name"]')?.value?.trim();
            if (discordId && name) names[discordId] = name;
        }
        return names;
    },
};

export const keyValueMapField = {
    render: ({ key, field, value }) => {
        const keyLabel = field.keyLabel || 'Clé';
        const valueLabel = field.valueLabel || 'Valeur';
        return html`
            <div class="keyvaluemap-list" data-field="${key}" data-type="keyvaluemap" data-key-label="${keyLabel}" data-value-label="${valueLabel}"
                data-role-use="${field.roleUse || ''}" data-role-keys="${field.roleKeys ? '1' : ''}" data-member-keys="${field.memberKeys ? '1' : ''}">
                <div class="keyvaluemap-header"><span>${keyLabel}</span><span>${valueLabel}</span><span></span></div>
                ${Object.entries(asMap(value)).map(([mapKey, mapValue]) => keyValueEntryHtml(field.roleUse || '',
                    !!field.roleKeys, !!field.memberKeys, keyLabel, valueLabel, mapKey, String(mapValue)))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addKeyValueMapEntry" data-click-args="${actionArgs(EL)}">+ Ajouter une entrée</button>`;
    },
    collect: (list) => {
        const map = {};
        for (const entry of list.querySelectorAll('.keyvaluemap-entry')) {
            const mapKey = entry.querySelector('[data-kvmap-field="key"]')?.value?.trim();
            const mapValue = entry.querySelector('[data-kvmap-field="value"]')?.value?.trim();
            if (mapKey && mapValue) map[mapKey] = mapValue;
        }
        return map;
    },
};

export function addKeyValueMapEntry(btn) {
    const list = btn.previousElementSibling;
    const d = list.dataset;
    list.insertAdjacentHTML('beforeend', String(keyValueEntryHtml(d.roleUse || '', !!d.roleKeys, !!d.memberKeys,
        d.keyLabel || 'Clé', d.valueLabel || 'Valeur', '', '')));
    const entry = list.lastElementChild;
    populateRolePickers(entry);
    populateMemberPickers(entry);
    entry.querySelector('input:not([hidden]), select')?.focus();
}

registerActions({
    addKeyValueMapEntry,
    addNameMapEntry,
});
