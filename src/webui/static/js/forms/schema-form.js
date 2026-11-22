// The schema-based settings form: each field's label and help, then its control.
//
// The controls and how they are read back live in forms/fields.js (the simple
// types) and in each widget's file (the lists), one object per type (P17).
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { html, raw } from '../core/html.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { fieldOf, numberRange } from './fields.js';

export { numberRange };

// A field's help as written in its schema: **gras**, `code` and line
// breaks, on top of escaped text (nothing else is markup).
export function mdInline(text) {
    return escapeHtml(String(text ?? ''))
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

function fieldHtml(formId, key, field, values, options) {
    const value = values[key];
    const hasValue = value !== undefined && value !== null;
    const type = field.type || 'string';
    if (field.hidden) {
        return hasValue ? html`<input type="hidden" data-field="${key}" data-type="${type}" value="${String(value)}">` : '';
    }
    // The label names its control (for=), so a click on it focuses the field
    // and a screen reader reads it with the field.
    const fieldId = `f-${formId}-${key}`.replace(/[^A-Za-z0-9_-]/g, '_');
    const resettable = RESETTABLE_TYPES.has(type) && field.default !== undefined && field.default !== null;
    const testable = options.module && state.templateTests?.[options.module]?.[key];
    const markup = String(html`
        <div class="form-field" data-key="${key}">
            <div class="form-field-header">
                <label class="form-label" for="${fieldId}">${field.label || key}${field.required && html` <span class="required-badge">requis</span>`}</label>
                ${resettable && html`<button type="button" class="btn-reset-default" data-click="resetFieldToDefault" data-click-args="${actionArgs(EL)}"
                    data-default="${JSON.stringify(field.default)}" title="Rétablir la valeur par défaut (à sauvegarder ensuite)">↺ Par défaut</button>`}
            </div>
            ${field.description && html`<div class="form-description">${raw(mdInline(field.description))}</div>`}
            ${fieldOf(type).render({ key, field, value, hasValue, fieldId, options })}
            ${testable && html`
            <button type="button" class="btn btn-ghost btn-sm template-test-btn" data-click="testTemplate" data-click-args="${actionArgs(options.serverId, options.module, key, EL)}"
                title="Le bot t'envoie en MP ce que donnent les messages ci-dessus, avec toi à la place du membre">✉️ Tester en MP</button>
            <div class="discord-preview" aria-live="polite" aria-label="Aperçu dans Discord"
                data-server="${options.serverId}" data-module="${options.module}" data-preview-field="${key}"></div>`}
        </div>`);
    // The field's first control; a widget without one (a list, say) is named
    // by aria-labelledby instead of the label's for=.
    const marker = `data-field="${key}"`;
    const opening = markup.indexOf(marker, markup.indexOf('</label>'));
    if (opening >= 0) return raw(markup.slice(0, opening) + `id="${fieldId}" ` + markup.slice(opening));
    return raw(markup
        .replace(`for="${fieldId}"`, `id="${fieldId}-label"`)
        .replace(`<div class="form-field" data-key="${key}">`,
            `<div class="form-field" data-key="${key}" role="group" aria-labelledby="${fieldId}-label">`));
}

export function renderSchemaForm(formId, schema, values, options = {}) {
    const entries = Object.entries(schema.fields || {})
        .filter(([key]) => !(options.excludeEnabled && key === 'enabled'));
    if (!entries.length) return String(html`<p class="form-empty">Aucun champ défini.</p>`);
    return String(html`<div class="schema-form" data-form-id="${formId}">${entries.map(([key, field]) => fieldHtml(formId, key, field, values, options))}</div>`);
}

// Field types whose default can be restored with one click.
export const RESETTABLE_TYPES = new Set(['text', 'string', 'number', 'boolean', 'url', 'choice']);

export async function resetFieldToDefault(btn) {
    const field = btn.closest('.form-field');
    const input = field?.querySelector('[data-field]');
    if (!input) return;
    let value;
    try { value = JSON.parse(btn.dataset.default); } catch { return; }
    if (input.type === 'checkbox') {
        input.checked = value === true;
    } else {
        const current = input.value;
        const next = value == null ? '' : String(value);
        if (current === next) {
            toast('Déjà à la valeur par défaut', 'info');
            return;
        }
        // Long texts (the AI persona) are easy to lose: confirm before overwriting.
        if (input.tagName === 'TEXTAREA' && current.trim()
            && !(await askConfirm('Remplacer le texte actuel par la valeur par défaut ?', { confirm: 'Remplacer', danger: true }))) {
            return;
        }
        input.value = next;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    toast('Valeur par défaut rétablie — pense à sauvegarder', 'success');
}

export function collectFormValues(formId) {
    const form = document.querySelector(`[data-form-id="${formId}"]`);
    if (!form) return {};
    const result = {};
    for (const el of form.querySelectorAll('[data-field]')) {
        const value = fieldOf(el.dataset.type).collect(el);
        if (value !== undefined) result[el.dataset.field] = value;
    }
    return result;
}

export function togglePasswordVisibility(btn) {
    const input = btn.previousElementSibling;
    input.type = input.type === 'password' ? 'text' : 'password';
}

registerActions({
    resetFieldToDefault,
    togglePasswordVisibility,
});
