// Embed manager entries.
import { registerActions } from '../../core/actions.js';
import { html } from '../../core/html.js';
import { discordEmbed, discordMessage } from '../../ui/discord.js';

export function addEmbedEntry(btn, fieldKey) {
    const list = btn.previousElementSibling;
    if (!list) return;
    const idx = list.querySelectorAll('.embedlist-entry').length;
    const entry = document.createElement('div');
    entry.className = 'embedlist-entry';
    entry.dataset.embedIdx = idx;
    entry.innerHTML = `
            <div class="embedlist-entry-header">
                <div class="embedlist-entry-title">Nouvel embed</div>
                <div class="embedlist-entry-controls">
                    <button class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.embedlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cet embed" aria-label="Supprimer cet embed">✕</button>
                </div>
            </div>
            <div class="embedlist-entry-fields">
                <div><label style="font-size:11px;color:var(--text-muted)">Titre</label>
                <input type="text" class="form-input" data-embed-field="title" value="Nouvel embed" placeholder="Titre de l'embed"></div>
                <div><label style="font-size:11px;color:var(--text-muted)">Couleur</label>
                <div class="embedlist-color-picker">
                    <input type="color" class="embedlist-color-input" data-embed-field="color" value="#3498db" data-change="updateEmbedColorText" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <div class="embedlist-color-text">3498db</div>
                </div></div>
            </div>
            <div class="embedlist-links">
                <div class="embedlist-links-label">Liens</div>
            </div>
            <button type="button" class="btn-add-link" data-click="addEmbedLink" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un lien</button>
        `;
    list.appendChild(entry);
    refreshEmbedPreview(entry);

    // Update title when title input changes
    const titleInput = entry.querySelector('[data-embed-field="title"]');
    titleInput.addEventListener('change', (e) => {
        entry.querySelector('.embedlist-entry-title').textContent = e.target.value || 'Nouvel embed';
    });
    titleInput.focus();
}

export function addEmbedLink(btn) {
    const embedEntry = btn.closest('.embedlist-entry');
    if (!embedEntry) return;
    const linkContainer = embedEntry.querySelector('.embedlist-links');
    const idx = linkContainer.querySelectorAll('.embedlist-link-entry').length;
    const linkEntry = document.createElement('div');
    linkEntry.className = 'embedlist-link-entry';
    linkEntry.dataset.linkIdx = idx;
    linkEntry.innerHTML = `
            <input type="text" data-link-field="title" value="" placeholder="Titre du lien" maxlength="256">
            <input type="text" data-link-field="url" value="" placeholder="https://exemple.com" maxlength="2048">
            <button type="button" class="btn-remove-link" data-click="remove-closest" data-click-args="[&quot;.embedlist-link-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
        `;
    linkContainer.appendChild(linkEntry);
    linkEntry.querySelector('input').focus();
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
