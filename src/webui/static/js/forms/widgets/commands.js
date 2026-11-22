// Custom commands.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';
import { multiPickerHtml, multiPickerIds, populateMultiPickers } from '../pickers/lists.js';

const TARGETS = { none: 'Aucune', optional: 'Facultative', required: 'Obligatoire' };
const MAX_COOLDOWN = 3600;

// A command's target, cooldown and roles (A9), under its name.
export function commandOptionsHtml(cmd = {}) {
    const target = TARGETS[cmd.target] ? cmd.target : 'none';
    const cooldown = Number.isInteger(cmd.cooldown) ? cmd.cooldown : 0;
    const roles = Array.isArray(cmd.roles) ? cmd.roles.map(String) : [];
    return html`
            <div class="commandlist-entry-fields">
                <div><label class="commandlist-field-label">Membre visé (<code>cible</code>)</label>
                <select class="form-input" data-cmd-field="target" aria-label="Membre visé">
                    ${Object.entries(TARGETS).map(([value, label]) => html`<option value="${value}" ${value === target ? raw('selected') : ''}>${label}</option>`)}
                </select></div>
                <div><label class="commandlist-field-label">Délai entre deux usages (secondes)</label>
                <input type="number" class="form-input" data-cmd-field="cooldown" value="${cooldown}" min="0" max="${MAX_COOLDOWN}" step="1" aria-label="Délai entre deux usages, en secondes">
                <div class="commandlist-field-label">Entre 0 et ${MAX_COOLDOWN.toLocaleString('fr-FR')} (0 : aucun).</div></div>
            </div>
            <div class="commandlist-roles"><label class="commandlist-field-label">Réservée aux rôles (vide : tout le monde)</label>
            ${raw(multiPickerHtml(null, 'rolelist', roles, 'staff'))}</div>`;
}

// What commandOptionsHtml() holds, as the schema stores it.
export function commandOptions(entry) {
    const target = entry.querySelector('[data-cmd-field="target"]')?.value;
    const cooldown = Math.round(Number(entry.querySelector('[data-cmd-field="cooldown"]')?.value || 0));
    const picker = entry.querySelector('.commandlist-roles .multi-picker');
    return {
        target: TARGETS[target] ? target : 'none',
        cooldown: Number.isFinite(cooldown) ? Math.min(Math.max(cooldown, 0), MAX_COOLDOWN) : 0,
        roles: picker ? multiPickerIds(picker) : [],
    };
}

function responseRow(text) {
    return html`
            <input type="text" data-response-field="text" value="${text}" placeholder="{auteur} fait un câlin à {cible} https://exemple.com/calin.gif" maxlength="2000">
            <button type="button" class="btn-remove-response" data-click="remove-closest" data-click-args="${actionArgs('.commandlist-response-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>`;
}

// One command: its name, description, options and the responses picked from.
function commandEntryInner(cmd = {}) {
    const name = typeof cmd.name === 'string' ? cmd.name : '';
    const description = typeof cmd.description === 'string' ? cmd.description : '';
    const responses = Array.isArray(cmd.responses) ? cmd.responses : [];
    return html`
            <div class="commandlist-entry-header">
                <div class="commandlist-entry-title">/${name || 'nouvelle-commande'}</div>
                <button type="button" class="btn-remove-embed" data-click="remove-closest" data-click-args="${actionArgs('.commandlist-entry', EL)}" title="Supprimer cette commande" aria-label="Supprimer cette commande">✕</button>
            </div>
            <div class="commandlist-entry-fields">
                <div><label class="commandlist-field-label">Nom</label>
                <input type="text" class="form-input" data-cmd-field="name" value="${name}" placeholder="blague" maxlength="32" data-input="updateCommandTitle" data-input-args="${actionArgs(EL)}"></div>
                <div><label class="commandlist-field-label">Description</label>
                <input type="text" class="form-input" data-cmd-field="description" value="${description}" placeholder="Raconte une blague au hasard" maxlength="100"></div>
            </div>
            ${commandOptionsHtml(cmd)}
            <div class="commandlist-responses">
                <div class="commandlist-responses-label">Réponses (tirées au hasard)</div>
                ${responses.map(r => html`<div class="commandlist-response-entry">${responseRow(typeof r === 'string' ? r : String(r?.text ?? ''))}</div>`)}
            </div>
            <button type="button" class="btn-add-link" data-click="addCommandResponse" data-click-args="${actionArgs(EL)}">+ Ajouter une réponse</button>`;
}

export const commandListField = {
    render: ({ key, value }) => html`
            <div class="commandlist-list" data-field="${key}" data-type="commandlist">
                ${(Array.isArray(value) ? value : []).map(cmd => html`<div class="commandlist-entry">${commandEntryInner(cmd)}</div>`)}
            </div>
            <button type="button" class="btn-add-embed" data-click="addCommandEntry" data-click-args="${actionArgs(EL)}">+ Ajouter une commande</button>`,
    // Always a list: removing the last command must persist as [].
    collect: (list) => {
        const commands = [];
        for (const entry of list.querySelectorAll('.commandlist-entry')) {
            const name = entry.querySelector('[data-cmd-field="name"]')?.value?.trim();
            if (!name) continue;
            const description = entry.querySelector('[data-cmd-field="description"]')?.value?.trim();
            const responses = [...entry.querySelectorAll('.commandlist-response-entry [data-response-field="text"]')]
                .map(i => i.value.trim()).filter(Boolean);
            commands.push({ name, description: description || '', responses, ...commandOptions(entry) });
        }
        return commands;
    },
};

export function addCommandEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list) return;
    const entry = document.createElement('div');
    entry.className = 'commandlist-entry';
    entry.innerHTML = String(commandEntryInner());
    list.appendChild(entry);
    populateMultiPickers(entry);
    // A command without a response is dropped at load, so start with one.
    addCommandResponse(entry.querySelector('.btn-add-link'));
    entry.querySelector('[data-cmd-field="name"]').focus();
}

export function addCommandResponse(btn) {
    const commandEntry = btn.closest('.commandlist-entry');
    if (!commandEntry) return;
    const container = commandEntry.querySelector('.commandlist-responses');
    const responseEntry = document.createElement('div');
    responseEntry.className = 'commandlist-response-entry';
    responseEntry.innerHTML = String(responseRow(''));
    container.appendChild(responseEntry);
    responseEntry.querySelector('input').focus();
}

export function updateCommandTitle(input) {
    const entry = input.closest('.commandlist-entry');
    const title = entry?.querySelector('.commandlist-entry-title');
    if (title) {
        // Mirror the server-side normalisation: lowercase, no spaces.
        title.textContent = '/' + (input.value.trim().toLowerCase().replace(/\s+/g, '-') || 'nouvelle-commande');
    }
}

registerActions({
    addCommandEntry,
    addCommandResponse,
    updateCommandTitle,
});
