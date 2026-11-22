// Weighted message lists and template variables.
import { EL, EVENT, actionArgs, registerActions } from '../../core/actions.js';
import { html } from '../../core/html.js';

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

function messageRow(text, weight) {
    return html`
            <input type="text" data-msg-field="message" value="${text}" placeholder="Message…">
            <input type="number" data-msg-field="weight" value="${weight}" min="0" max="1000" placeholder="1">
            <button class="btn-remove-msg" data-click="remove-closest" data-click-args="${actionArgs('.messagelist-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>`;
}

// A list of messages the bot picks from, each with its weight.
export const messageListField = {
    render: ({ key, field, value }) => {
        const variables = field.variables ? field.variables.split(',').map(v => v.trim()) : [];
        return html`
            <div class="messagelist-list" data-field="${key}" data-type="messagelist">
                <div class="messagelist-header"><span>Message</span><span>Poids</span><span></span></div>
                ${(Array.isArray(value) ? value : []).map((msg) => {
                    const text = typeof msg === 'string' ? msg : String(msg?.text ?? '');
                    const weight = (msg && typeof msg === 'object' && msg.weight !== undefined) ? msg.weight : 1;
                    return html`<div class="messagelist-entry">${messageRow(text, weight)}</div>`;
                })}
            </div>
            ${variables.length > 0 && html`<div class="messagelist-variables"><span>Variables : </span>${variables.map(v => html`<button type="button" class="var-chip" data-var="${v}" data-mousedown="insertVariable" data-mousedown-args="${actionArgs(EVENT, EL)}">${v}</button>`)}</div>`}
            <button type="button" class="btn-add-entry" data-click="addMessageEntry" data-click-args="${actionArgs(EL)}">+ Ajouter un message</button>`;
    },
    collect: (list) => {
        const messages = [];
        for (const entry of list.querySelectorAll('.messagelist-entry')) {
            const text = entry.querySelector('[data-msg-field="message"]')?.value?.trim();
            const weight = entry.querySelector('[data-msg-field="weight"]')?.value?.trim();
            if (text) messages.push({ text, weight: weight ? Number(weight) : 1 });
        }
        return messages.length ? messages : undefined;
    },
};

export function addMessageEntry(btn) {
    // Find the messagelist-list container (may have a variables div in between)
    let list = btn.previousElementSibling;
    while (list && !list.classList.contains('messagelist-list')) {
        list = list.previousElementSibling;
    }
    if (!list) return;
    const entry = document.createElement('div');
    entry.className = 'messagelist-entry';
    entry.innerHTML = String(messageRow('', 1));
    list.appendChild(entry);
    entry.querySelector('input').focus();
}

registerActions({
    addMessageEntry,
    insertVariable,
});
