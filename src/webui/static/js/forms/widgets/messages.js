// Weighted message lists and template variables.
import { registerActions } from '../../core/actions.js';

export function insertVariable(event, btn) {
    event.preventDefault();
    const variable = btn.dataset.var;
    const input = document.activeElement;
    if (!input || (input.tagName !== 'INPUT' && input.tagName !== 'TEXTAREA')) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = input.value.slice(0, start) + variable + input.value.slice(end);
    input.selectionStart = input.selectionEnd = start + variable.length;
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

export function addMessageEntry(btn) {
    // Find the messagelist-list container (may have a variables div in between)
    let list = btn.previousElementSibling;
    while (list && !list.classList.contains('messagelist-list')) {
        list = list.previousElementSibling;
    }
    if (!list) return;
    const entry = document.createElement('div');
    entry.className = 'messagelist-entry';
    entry.innerHTML = `
            <input type="text" data-msg-field="message" value="" placeholder="Message…">
            <input type="number" data-msg-field="weight" value="1" min="0" max="1000" placeholder="1">
            <button class="btn-remove-msg" data-click="remove-closest" data-click-args="[&quot;.messagelist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
        `;
    list.appendChild(entry);
    entry.querySelector('input').focus();
}

registerActions({
    addMessageEntry,
    insertVariable,
});
