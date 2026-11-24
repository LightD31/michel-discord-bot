// HTML built from a template, escaped by default.
//
//     html`<p title="${name}">${text}</p>`
//
// Every value interpolated into html`…` is escaped, so a guild, member or
// config value can't add markup: forgetting is no longer stored XSS. What is
// already HTML passes through when it says so: another html`…`, an array of
// them (a list's rows), or raw(text) for markup built some other way. false,
// null and undefined render nothing, so `${cond && html`…`}` works.
// The result is a SafeHtml that reads as its text wherever a string is
// expected (innerHTML, a `…${x}…` template), so it mixes with older code.
import { escapeHtml } from './util.js';

export class SafeHtml {
    constructor(text) {
        this.text = text;
    }

    toString() {
        return this.text;
    }
}

// Markup that is already safe: escaped elsewhere, or written by the app itself.
export function raw(text) {
    return text instanceof SafeHtml ? text : new SafeHtml(String(text ?? ''));
}

function piece(value) {
    if (value instanceof SafeHtml) return value.text;
    if (Array.isArray(value)) return value.map(piece).join('');
    if (value === null || value === undefined || value === false) return '';
    return escapeHtml(value);
}

export function html(strings, ...values) {
    let out = strings[0];
    values.forEach((value, i) => {
        out += piece(value) + strings[i + 1];
    });
    return new SafeHtml(out);
}
