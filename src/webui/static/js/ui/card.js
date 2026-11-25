// A dashboard card loaded from the API (P41): one frame, one loader, one style for
// "nothing yet" and "unavailable", instead of each card writing its own.
import { api } from '../core/api.js';
import { html, raw } from '../core/html.js';

export const SPINNER = '<div class="loading"><div class="spinner"></div></div>';
export const UNAVAILABLE = 'Indisponible pour le moment : réessaie dans un instant.';

// The frame: a header (title, subtitle, tools on the right) and a body to fill,
// with a spinner until loadCard() does. *tools* and *body* are markup.
export function cardHtml({ id = '', bodyId, title, subtitle = '', tools = '', body = SPINNER, className = '' }) {
    return html`
        <div class="${className ? `${className} ` : ''}card"${raw(id ? String(html` id="${id}"`) : '')}>
            <div class="card-header">
                <div>
                    <div class="card-title">${title}</div>
                    ${subtitle && html`<div class="card-subtitle">${subtitle}</div>`}
                </div>
                ${tools && html`<div class="card-actions">${raw(String(tools))}</div>`}
            </div>
            <div id="${bodyId}">${raw(String(body))}</div>
        </div>`;
}

// A line in the card's body when there is nothing to show, or nothing could be read.
export function noteHtml(text) {
    return html`<p class="me-empty">${text}</p>`;
}

// Fill the card's body from *url*: render(data), or *empty* when isEmpty(data), or
// *unavailable* when the request fails. Nothing is drawn if the page was redrawn
// meanwhile (another card has the id). Returns the data, or null after a failure.
export async function loadCard(bodyId, url, render, { empty = '', isEmpty = () => false, unavailable = UNAVAILABLE } = {}) {
    const el = document.getElementById(bodyId);
    if (!el) return null;
    let data;
    try {
        data = await api(url, { quiet: true });
    } catch (e) {
        if (document.getElementById(bodyId) === el) el.innerHTML = String(noteHtml(unavailable));
        return null;
    }
    if (document.getElementById(bodyId) !== el) return data;
    el.innerHTML = String(empty && isEmpty(data) ? noteHtml(empty) : await render(data));
    return data;
}
