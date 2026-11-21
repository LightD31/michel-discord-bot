// Custom commands.
import { registerActions } from '../../core/actions.js';

export function addCommandEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list) return;
    const entry = document.createElement('div');
    entry.className = 'commandlist-entry';
    entry.innerHTML = `
            <div class="commandlist-entry-header">
                <div class="commandlist-entry-title">/nouvelle-commande</div>
                <button type="button" class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.commandlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cette commande" aria-label="Supprimer cette commande">✕</button>
            </div>
            <div class="commandlist-entry-fields">
                <div><label class="commandlist-field-label">Nom</label>
                <input type="text" class="form-input" data-cmd-field="name" value="" placeholder="blague" maxlength="32" data-input="updateCommandTitle" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]"></div>
                <div><label class="commandlist-field-label">Description</label>
                <input type="text" class="form-input" data-cmd-field="description" value="" placeholder="Raconte une blague au hasard" maxlength="100"></div>
            </div>
            <div class="commandlist-responses">
                <div class="commandlist-responses-label">Réponses (tirées au hasard)</div>
            </div>
            <button type="button" class="btn-add-link" data-click="addCommandResponse" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une réponse</button>
        `;
    list.appendChild(entry);
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
    responseEntry.innerHTML = `
            <input type="text" data-response-field="text" value="" placeholder="https://exemple.com/gif.gif ou un texte" maxlength="2000">
            <button type="button" class="btn-remove-response" data-click="remove-closest" data-click-args="[&quot;.commandlist-response-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
        `;
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
