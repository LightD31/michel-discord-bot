// Discord accounts named by id (P17): the global settings' bot, owner and developers.
// No server's member search fits them, so an id is typed and the account it
// names is shown next to it, as Discord knows it (GET /api/accounts).
import { api } from '../../core/api.js';
import { html } from '../../core/html.js';

const NAMES = new Map();  // id -> {name, username} | null (unknown)
const DELAY_MS = 400;
let timer = null;

function idsOf(input) {
    return input.value.split(/[\s,]+/).map(s => s.trim()).filter(s => /^\d{15,20}$/.test(s));
}

export function accountFieldHtml(key, value, { multi = false } = {}) {
    const text = multi ? (Array.isArray(value) ? value.join('\n') : '') : (value ?? '');
    const control = multi
        ? html`<textarea class="form-textarea" data-field="${key}" data-type="list" data-account rows="3"
                placeholder="Un identifiant Discord par ligne">${text}</textarea>`
        : html`<input type="text" class="form-input" data-field="${key}" data-type="string" data-account
                value="${text}" placeholder="Identifiant Discord (clic droit → Copier l'identifiant)" inputmode="numeric">`;
    return html`${control}<div class="account-names" aria-live="polite"></div>`;
}

function namesHtml(ids) {
    return html`${ids.map(id => {
        const found = NAMES.get(id);
        if (found === undefined) return html`<span class="account-chip">${id} …</span>`;
        if (found === null) return html`<span class="account-chip account-unknown" title="${id}">❓ compte inconnu</span>`;
        const handle = found.username && found.username !== found.name ? ` (@${found.username})` : '';
        return html`<span class="account-chip" title="${id}">👤 ${found.name}${handle}</span>`;
    })}`;
}

export async function populateAccountNames(root = document) {
    const inputs = [...root.querySelectorAll('[data-account]')];
    const wanted = [...new Set(inputs.flatMap(idsOf))].filter(id => !NAMES.has(id));
    if (wanted.length) {
        try {
            const found = (await api(`/api/accounts?ids=${wanted.join(',')}`, { quiet: true })) || {};
            for (const id of wanted) NAMES.set(id, found[id] || null);
        } catch {
            return;  // the ids stay as typed
        }
    }
    for (const input of inputs) {
        const box = input.parentElement.querySelector('.account-names');
        if (box) box.innerHTML = String(namesHtml(idsOf(input)));
    }
}

document.addEventListener('input', (event) => {
    if (!(event.target instanceof Element) || !event.target.matches('[data-account]')) return;
    clearTimeout(timer);
    const root = event.target.closest('.modal-body, .main') || document;
    timer = setTimeout(() => populateAccountNames(root), DELAY_MS);
});
