// The Spotify user map.
import { registerActions } from '../../core/actions.js';
import { memberPickerHtml, populateMemberPickers, refreshMemberPickerGroup } from '../pickers/members.js';

export function addSpotifyUserEntry(btn) {
    const list = btn.previousElementSibling;
    const entry = document.createElement('div');
    entry.className = 'spotifymap-entry';
    entry.innerHTML = `
            <input type="text" data-spotifymap-field="spotifyId" value="" placeholder="ex: exemple123">
            ${memberPickerHtml('', 'data-spotifymap-field="discordId"')}
            <button class="btn-remove-entry" data-click="removeSpotifyMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
        `;
    list.appendChild(entry);
    populateMemberPickers(entry).then(() => refreshSpotifyMapListOptions(list));
    entry.querySelector('input').focus();
}

export function removeSpotifyMapEntry(btn) {
    const entry = btn.closest('.spotifymap-entry');
    if (!entry) return;
    const list = entry.closest('.spotifymap-list');
    entry.remove();
    if (list) refreshSpotifyMapListOptions(list);
}

export function refreshSpotifyMapListOptions(list) {
    if (list) refreshMemberPickerGroup(list.querySelectorAll('.member-picker'));
}

registerActions({
    addSpotifyUserEntry,
    removeSpotifyMapEntry,
});
