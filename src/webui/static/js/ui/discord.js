// A message drawn as Discord shows it (C4): the Markdown it reads, mentions
// by name, the author line. The text comes from the server, rendered by the
// bot's own function (POST …/template-preview); this only draws it.
//
// Safe by construction: the text is escaped first, and only this module's
// own fixed tags are added after. Links, mentions and code are cut out
// before the formatting runs, so a ** in a URL never becomes markup.
import { html, raw } from '../core/html.js';
import { state } from '../core/state.js';

// Marks a piece already turned into markup: a private-use character, removed
// from the text first, and never part of a URL (or markup would land in an href).
const SLOT = '\uE000';

// names: { users: Map(id → name), roles: Map, channels: Map }
function inline(text, names) {
    const slots = [];
    const keep = (markup) => `${SLOT}${slots.push(String(markup)) - 1}${SLOT}`;
    let out = String(text).replaceAll(SLOT, '');
    // Inline code first: nothing inside it is read.
    out = out.replace(/`([^`\n]+)`/g, (m, code) => keep(html`<code>${code}</code>`));
    out = out.replace(/<@!?(\d+)>/g, (m, id) => keep(
        html`<span class="dc-mention">@${names.users?.get(id) || 'utilisateur inconnu'}</span>`));
    out = out.replace(/<@&(\d+)>/g, (m, id) => keep(
        html`<span class="dc-mention">@${names.roles?.get(id) || 'rôle inconnu'}</span>`));
    out = out.replace(/<#(\d+)>/g, (m, id) => keep(
        html`<span class="dc-mention">#${names.channels?.get(id) || 'salon-inconnu'}</span>`));
    out = out.replace(/<a?:(\w{2,32}):\d+>/g, (m, name) => keep(html`:${name}:`));
    out = out.replace(/<t:(-?\d{1,13})(?::[tTdDfFR])?>/g, (m, seconds) => keep(
        html`<span class="dc-time">${new Date(Number(seconds) * 1000).toLocaleString('fr-FR')}</span>`));
    out = out.replace(/@(everyone|here)\b/g, (m) => keep(html`<span class="dc-mention">${m}</span>`));
    out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)\uE000]+)\)/g, (m, label, url) => keep(
        html`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`));
    out = out.replace(/https?:\/\/[^\s<\uE000]+[^\s<\uE000.,;:!?)\]'"]/g, (url) => keep(
        html`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
    // What is left is plain text: escaped, then given Discord's formatting.
    out = String(html`${out}`)
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/__(.+?)__/g, '<u>$1</u>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/(^|[^\w])_(.+?)_(?!\w)/g, '$1<em>$2</em>')
        .replace(/~~(.+?)~~/g, '<s>$1</s>')
        .replace(/\|\|(.+?)\|\|/g, '<span class="dc-spoiler">$1</span>');
    return out.replace(new RegExp(`${SLOT}(\\d+)${SLOT}`, 'g'), (m, i) => slots[Number(i)]);
}

// The Markdown of one message, as HTML (a SafeHtml).
export function discordMarkdown(text, names = {}) {
    const parts = String(text ?? '').split(/```(?:[\w+-]*\n)?([\s\S]*?)```/);
    const blocks = [];
    parts.forEach((part, i) => {
        if (i % 2) {
            blocks.push(String(html`<pre class="dc-codeblock"><code>${part.replace(/\n$/, '')}</code></pre>`));
            return;
        }
        let quote = [];
        const flush = () => {
            if (quote.length) blocks.push(`<blockquote>${quote.join('<br>')}</blockquote>`);
            quote = [];
        };
        const lines = part.split('\n');
        lines.forEach((line, n) => {
            const quoted = line.match(/^>\s?(.*)$/);
            if (quoted) {
                quote.push(inline(quoted[1], names));
                return;
            }
            flush();
            const heading = line.match(/^(#{1,3})\s+(.+)$/);
            const sub = line.match(/^-#\s+(.+)$/);
            const item = line.match(/^\s*[-*]\s+(.+)$/);
            if (heading) blocks.push(`<div class="dc-h${heading[1].length}">${inline(heading[2], names)}</div>`);
            else if (sub) blocks.push(`<div class="dc-sub">${inline(sub[1], names)}</div>`);
            else if (item) blocks.push(`<div class="dc-li">• ${inline(item[1], names)}</div>`);
            else if (line || n < lines.length - 1) blocks.push(`<div class="dc-line">${inline(line, names) || '&nbsp;'}</div>`);
        });
        flush();
    });
    return raw(blocks.join(''));
}

// A message from Michel, with the author line Discord shows.
export function discordMessage(content, { warnings = [], error = null } = {}) {
    const time = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    return html`
        <div class="dc-message">
            <div class="dc-avatar" aria-hidden="true">M</div>
            <div class="dc-body">
                <div class="dc-author"><span class="dc-name">${state.botInfo?.name || 'Michel'}</span>
                    <span class="dc-bot">APP</span><span class="dc-stamp">Aujourd'hui à ${time}</span></div>
                ${error
                    ? html`<div class="dc-error">❌ ${error}</div>`
                    : html`<div class="dc-content">${content}</div>`}
                ${warnings.map(w => html`<div class="dc-warning">⚠️ ${w}</div>`)}
            </div>
        </div>`;
}

// An embed as Discord frames it (P16): its colour stripe, title, text and fields.
// *description* is Markdown (drawn as the message's is); *color* six hex digits.
export function discordEmbed({ title = '', description = '', color = '', author = '', fields = [], footer = '' }, names = {}) {
    const hex = /^[0-9a-fA-F]{6}$/.test(String(color)) ? `#${color}` : null;
    return html`
        <div class="dc-embed" ${hex ? raw(`style="border-left-color:${hex}"`) : ''}>
            ${author && html`<div class="dc-embed-author">${author}</div>`}
            ${title && html`<div class="dc-embed-title">${title}</div>`}
            ${description && html`<div class="dc-embed-text">${discordMarkdown(description, names)}</div>`}
            ${fields.map(f => html`<div class="dc-embed-field"><div class="dc-embed-field-name">${f.name}</div>
                <div class="dc-embed-text">${discordMarkdown(f.value, names)}</div></div>`)}
            ${footer && html`<div class="dc-embed-foot">${footer}</div>`}
        </div>`;
}

// The components under a message: a row of buttons, or a drop-down.
export function discordButtons(buttons) {
    return html`<div class="dc-buttons">${buttons.map(b => html`
        <span class="dc-button">${b.emoji && html`<span aria-hidden="true">${b.emoji}</span> `}${b.label}</span>`)}</div>`;
}

export function discordSelect(placeholder) {
    return html`<div class="dc-select"><span>${placeholder}</span><span aria-hidden="true">▾</span></div>`;
}
