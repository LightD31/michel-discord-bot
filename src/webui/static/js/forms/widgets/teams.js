// The esport team list: each team, its VLR.gg id and its planning channel.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html } from '../../core/html.js';
import { channelPickerHtml, channelPickerValue, populateChannelPickers } from '../pickers/channels.js';

export function renderTeamEntry(t, idx) {
    t = t || {};
    return html`
            <div class="team-entry" data-team-idx="${idx}">
                <div class="team-entry-grid">
                    <div class="team-field">
                        <label>Nom</label>
                        <input type="text" data-team-field="name" value="${t.name || ''}" placeholder="ex: Mandatory">
                    </div>
                    <div class="team-field">
                        <label>ID VLR.gg</label>
                        <input type="text" data-team-field="vlrTeamId" value="${t.vlrTeamId || ''}" placeholder="ex: 7967">
                    </div>
                    <div class="team-field team-field-channel">
                        <label>Salon planning</label>
                        ${channelPickerHtml(html`data-team-field="channelId"`, t.channelId)}
                    </div>
                </div>
                <div class="team-entry-footer">
                    <label class="streamer-toggle-inline">
                        <input type="checkbox" data-team-field="pin" ${t.pin ? 'checked' : ''}>
                        <span>Épingler le message</span>
                    </label>
                    <button class="btn-remove-team" data-click="remove-closest" data-click-args="${actionArgs('.team-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
            </div>`;
}

export const teamsField = {
    render: ({ key, value }) => html`
            <div class="teams-list" data-field="${key}" data-type="teams">
                ${(Array.isArray(value) ? value : []).map((t, idx) => renderTeamEntry(t, idx))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addTeamEntry" data-click-args="${actionArgs(EL)}">+ Ajouter une équipe</button>`,
    collect: (list) => {
        const teams = [];
        for (const entry of list.querySelectorAll('.team-entry')) {
            const name = entry.querySelector('[data-team-field="name"]')?.value?.trim();
            if (!name) continue;
            const team = { name };
            const vlrTeamId = entry.querySelector('[data-team-field="vlrTeamId"]')?.value?.trim();
            const channelId = channelPickerValue(entry.querySelector('.channel-picker[data-team-field="channelId"]'));
            if (vlrTeamId) team.vlrTeamId = vlrTeamId;
            if (channelId) team.channelId = channelId;
            if (entry.querySelector('input[type="checkbox"][data-team-field="pin"]')?.checked) team.pin = true;
            teams.push(team);
        }
        return teams;
    },
};

export function addTeamEntry(btn) {
    const list = btn.previousElementSibling;
    const idx = list.querySelectorAll('.team-entry').length;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = String(renderTeamEntry({}, idx)).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    populateChannelPickers(entry);
    entry.querySelector('input[data-team-field="name"]').focus();
}

registerActions({
    addTeamEntry,
});
