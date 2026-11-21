// Embed manager entries.
import { registerActions } from '../../core/actions.js';

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
