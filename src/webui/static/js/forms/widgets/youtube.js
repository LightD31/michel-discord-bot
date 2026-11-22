// The YouTube channel list: each channel, what it posts about, the role it pings.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';
import { populateRolePickers } from '../pickers/lists.js';
import { rolePickerHtml } from '../pickers/roles.js';

export function renderYoutubeChannelEntry(handle, cfg) {
    const toggle = (field, label, on) => html`
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="${field}" ${on ? 'checked' : ''}>
                    <span>${label}</span>
                </label>`;
    return html`
            <div class="youtubechannel-entry">
                <input type="text" data-yt-field="handle" value="${handle || ''}" placeholder="@handle">
                <input type="text" data-yt-field="label" value="${cfg.label || ''}" placeholder="Libellé (optionnel)">
                ${toggle('shorts', 'Shorts', !!cfg.shorts)}
                ${toggle('live', 'Live', !!cfg.live)}
                ${toggle('vod', 'VOD', cfg.vod !== false)}
                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="${actionArgs('.youtubechannel-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                <div class="youtubechannel-alert">
                    <label>Rôle à mentionner à chaque vidéo</label>
                    ${raw(rolePickerHtml(cfg.mentionRoleId ? String(cfg.mentionRoleId) : '', 'data-yt-field="mentionRoleId"', 'mention'))}
                </div>
            </div>`;
}

export const youtubeChannelMapField = {
    render: ({ key, value }) => {
        const channels = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
        return html`
            <div class="youtubechannel-list" data-field="${key}" data-type="youtubechannelmap">
                ${Object.entries(channels).map(([handle, cfg]) => renderYoutubeChannelEntry(handle, cfg || {}))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addYoutubeChannelEntry" data-click-args="${actionArgs(EL)}">+ Ajouter une chaîne</button>`;
    },
    collect: (list) => {
        const channels = {};
        for (const entry of list.querySelectorAll('.youtubechannel-entry')) {
            const value = (field) => entry.querySelector(`[data-yt-field="${field}"]`);
            const handle = value('handle')?.value?.trim();
            if (!handle) continue;
            const cfg = { shorts: !!value('shorts')?.checked, live: !!value('live')?.checked, vod: !!value('vod')?.checked };
            const label = value('label')?.value?.trim();
            const mentionRoleId = value('mentionRoleId')?.value?.trim();
            if (label) cfg.label = label;
            if (mentionRoleId) cfg.mentionRoleId = mentionRoleId;
            channels[handle] = cfg;
        }
        return channels;
    },
};

export function addYoutubeChannelEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('youtubechannel-list')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = String(renderYoutubeChannelEntry('', { shorts: false, live: false, vod: true })).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    populateRolePickers(entry);
    entry.querySelector('input[type="text"]').focus();
}

registerActions({
    addYoutubeChannelEntry,
});
