// Embed manager entries.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html } from '../../core/html.js';
import { discordEmbed, discordMessage } from '../../ui/discord.js';

function linkRow(title, url, idx) {
    return html`
            <div class="embedlist-link-entry" data-link-idx="${idx}">
                <input type="text" data-link-field="title" value="${title}" placeholder="Titre du lien" maxlength="256">
                <input type="text" data-link-field="url" value="${url}" placeholder="https://exemple.com" maxlength="2048">
                <button type="button" class="btn-remove-link" data-click="remove-closest" data-click-args="${actionArgs('.embedlist-link-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>`;
}

// One embed of the Embed manager: its title, colour and links.
function embedEntryInner(embed) {
    const title = embed.title || 'Sans titre';
    // Free text in the stored config: anything but six hex digits falls back to the default.
    const raw = String(embed.color || '').replace(/^#/, '');
    const color = /^[0-9a-fA-F]{6}$/.test(raw) ? raw : '3498db';
    const links = Array.isArray(embed.links) ? embed.links : [];
    return html`
            <div class="embedlist-entry-header">
                <div class="embedlist-entry-title" title="${title}">${title}</div>
                <div class="embedlist-entry-controls">
                    <button class="btn-remove-embed" data-click="remove-closest" data-click-args="${actionArgs('.embedlist-entry', EL)}" title="Supprimer cet embed" aria-label="Supprimer cet embed">✕</button>
                </div>
            </div>
            <div class="embedlist-entry-fields">
                <div><label class="embedlist-field-label">Titre</label>
                <input type="text" class="form-input" data-embed-field="title" value="${title}" placeholder="Titre de l'embed"></div>
                <div><label class="embedlist-field-label">Couleur</label>
                <div class="embedlist-color-picker">
                    <input type="color" class="embedlist-color-input" data-embed-field="color" value="#${color}" data-change="updateEmbedColorText" data-change-args="${actionArgs(EL)}">
                    <div class="embedlist-color-text">${color}</div>
                </div></div>
            </div>
            <div class="embedlist-links">
                <div class="embedlist-links-label">Liens</div>
                ${links.map((link, idx) => linkRow(link.title || '', link.url || '', idx))}
            </div>
            <button type="button" class="btn-add-link" data-click="addEmbedLink" data-click-args="${actionArgs(EL)}">+ Ajouter un lien</button>`;
}

export const embedListField = {
    render: ({ key, value }) => html`
            <div class="embedlist-list" data-field="${key}" data-type="embedlist">
                ${(Array.isArray(value) ? value : []).map((embed, idx) => html`<div class="embedlist-entry" data-embed-idx="${idx}">${embedEntryInner(embed)}</div>`)}
            </div>
            <button type="button" class="btn-add-embed" data-click="addEmbedEntry" data-click-args="${actionArgs(EL, key)}">+ Ajouter un embed</button>`,
    collect: (list) => {
        const embeds = [];
        for (const entry of list.querySelectorAll('.embedlist-entry')) {
            const title = entry.querySelector('[data-embed-field="title"]')?.value?.trim();
            const color = (entry.querySelector('[data-embed-field="color"]')?.value || '#3498db').replace(/^#/, '');
            const links = [...entry.querySelectorAll('.embedlist-link-entry')]
                .map(row => ({
                    title: row.querySelector('[data-link-field="title"]')?.value?.trim(),
                    url: row.querySelector('[data-link-field="url"]')?.value?.trim(),
                }))
                .filter(link => link.title && link.url);
            const embed = { title: title || 'Embed', color };
            if (links.length) embed.links = links;
            embeds.push(embed);
        }
        return embeds.length ? embeds : undefined;
    },
};

export function addEmbedEntry(btn, fieldKey) {
    const list = btn.previousElementSibling;
    if (!list) return;
    const entry = document.createElement('div');
    entry.className = 'embedlist-entry';
    entry.dataset.embedIdx = list.querySelectorAll('.embedlist-entry').length;
    entry.innerHTML = String(embedEntryInner({ title: 'Nouvel embed' }));
    list.appendChild(entry);
    refreshEmbedPreview(entry);
    const titleInput = entry.querySelector('[data-embed-field="title"]');
    titleInput.addEventListener('change', (e) => {
        entry.querySelector('.embedlist-entry-title').textContent = e.target.value || 'Nouvel embed';
    });
    titleInput.focus();
}

export function addEmbedLink(btn) {
    const embedEntry = btn.closest('.embedlist-entry');
    if (!embedEntry) return;
    const container = embedEntry.querySelector('.embedlist-links');
    const idx = container.querySelectorAll('.embedlist-link-entry').length;
    container.insertAdjacentHTML('beforeend', String(linkRow('', '', idx)));
    container.lastElementChild.querySelector('input').focus();
}

// ── Preview (P16) ────────────────────────────────
// Each embed as Discord will show it once published: built as
// src/discord_ext/embedmanager.py builds it (a field per link with a URL).
export function embedPreviewHtml(entry) {
    const value = (sel) => entry.querySelector(sel)?.value?.trim() || '';
    const links = [...entry.querySelectorAll('.embedlist-link-entry')]
        .map(row => ({
            title: row.querySelector('[data-link-field="title"]')?.value?.trim() || 'Lien',
            url: row.querySelector('[data-link-field="url"]')?.value?.trim() || '',
        }))
        .filter(link => link.url);
    const embed = discordEmbed({
        title: value('[data-embed-field="title"]') || 'Sans titre',
        color: value('[data-embed-field="color"]').replace(/^#/, ''),
        // The link itself is shortened when published: the preview shows where it leads.
        fields: links.map(link => ({ name: link.title, value: '[Ouvrir le lien](' + link.url + ')' })),
    });
    return discordMessage(embed);
}

export function refreshEmbedPreview(entry) {
    let box = entry.querySelector(':scope > .embed-preview');
    if (!box) {
        box = document.createElement('div');
        box.className = 'discord-preview embed-preview';
        box.setAttribute('aria-label', 'Aperçu dans Discord');
        entry.appendChild(box);
    }
    box.innerHTML = String(html`${embedPreviewHtml(entry)}`);
}

export function refreshEmbedPreviews(root = document) {
    for (const entry of root.querySelectorAll('.embedlist-entry')) refreshEmbedPreview(entry);
}

for (const type of ['input', 'change', 'click']) {
    document.addEventListener(type, (event) => {
        const entry = event.target instanceof Element ? event.target.closest('.embedlist-entry') : null;
        if (entry && entry.isConnected) refreshEmbedPreview(entry);
    });
}

export function updateEmbedColorText(input) {
    const color = input.value.substring(1); // Remove # from color
    const textDiv = input.nextElementSibling;
    if (textDiv) {
        textDiv.textContent = color;
    }
}

registerActions({
    addEmbedEntry,
    addEmbedLink,
    updateEmbedColorText,
});
