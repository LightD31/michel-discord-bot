// The Spotify user map.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';
import { memberPickerHtml, memberPickerValue, populateMemberPickers, refreshMemberPickerGroup } from '../pickers/members.js';

function spotifyUserRow(user) {
    const inner = html`
            <input type="text" data-spotifymap-field="spotifyId" value="${user.spotifyId || ''}" placeholder="ex: exemple123">
            ${raw(memberPickerHtml(user.discordId || '', 'data-spotifymap-field="discordId"'))}
            <button class="btn-remove-entry" data-click="removeSpotifyMapEntry" data-click-args="${actionArgs(EL)}" title="Supprimer" aria-label="Supprimer">✕</button>`;
    return { inner };
}

export const spotifyMapField = {
    render: ({ key, value }) => html`
            <div class="spotifymap-list" data-field="${key}" data-type="spotifymap">
                <div class="spotifymap-header"><span>Spotify ID</span><span>Membre Discord</span><span></span></div>
                ${(Array.isArray(value) ? value : []).map((user, idx) => html`
                <div class="spotifymap-entry" data-user-idx="${idx}">${spotifyUserRow(user).inner}</div>`)}
            </div>
            <button type="button" class="btn-add-entry" data-click="addSpotifyUserEntry" data-click-args="${actionArgs(EL)}">+ Ajouter un utilisateur</button>`,
    collect: (list) => {
        const users = [];
        for (const entry of list.querySelectorAll('.spotifymap-entry')) {
            const spotifyId = entry.querySelector('[data-spotifymap-field="spotifyId"]')?.value?.trim();
            const discordId = memberPickerValue(entry.querySelector('.member-picker'));
            if (spotifyId && discordId) users.push({ spotifyId, discordId });
        }
        return users.length ? users : undefined;
    },
};

export function addSpotifyUserEntry(btn) {
    const list = btn.previousElementSibling;
    const entry = document.createElement('div');
    entry.className = 'spotifymap-entry';
    entry.innerHTML = String(spotifyUserRow({}).inner);
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
