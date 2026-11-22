// The Twitch streamer list.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';
import { state } from '../../core/state.js';
import { channelPickerHtml, channelPickerValue, populateChannelPickers } from '../pickers/channels.js';
import { populateRolePickers } from '../pickers/lists.js';
import { rolePickerHtml } from '../pickers/roles.js';

export const STREAMER_TOGGLE_FIELDS = [
    {key: 'twitchPlanningPinMessage', label: 'Épingler le planning', planningOnly: true},
    {key: 'notifyStreamStart', label: 'Début de live'},
    {key: 'notifyStreamUpdate', label: 'Changements live'},
    {key: 'notifyStreamEnd', label: 'Fin de live'},
    {key: 'notifyEmoteChanges', label: 'Emotes'},
    {key: 'manageDiscordEvents', label: 'Événement Discord'},
];

export function renderStreamerEntry(login, cfg) {
    const planningChan = cfg.twitchPlanningChannelId ? String(cfg.twitchPlanningChannelId) : '';
    const notifChan = cfg.twitchNotificationChannelId ? String(cfg.twitchNotificationChannelId) : '';
    const planningEnabled = !!planningChan;
    const mentionRole = cfg.twitchMentionRoleId ? String(cfg.twitchMentionRoleId) : '';
    const template = typeof cfg.twitchNotificationTemplate === 'string' ? cfg.twitchNotificationTemplate : '';
    return html`
            <div class="streamer-entry${planningEnabled ? '' : ' planning-disabled'}">
                <div class="streamer-entry-header">
                    <input type="text" data-streamer-field="login" value="${login || ''}" placeholder="Login Twitch (ex: zerator)">
                    <button class="btn-remove-streamer" data-click="remove-closest" data-click-args="${actionArgs('.streamer-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
                <label class="streamer-planning-toggle">
                    <input type="checkbox" data-streamer-planning-enabled ${planningEnabled ? 'checked' : ''} data-change="toggleStreamerPlanning" data-change-args="${actionArgs(EL)}">
                    <span>Activer le message de planning</span>
                </label>
                <div class="streamer-entry-grid">
                    <div class="streamer-field planning-only">
                        <label>Salon planning</label>
                        ${channelPickerHtml(html`data-streamer-field="twitchPlanningChannelId"`, planningChan)}
                    </div>
                    <div class="streamer-field">
                        <label>Salon notifications</label>
                        ${channelPickerHtml(html`data-streamer-field="twitchNotificationChannelId"`, notifChan)}
                    </div>
                </div>
                <div class="streamer-toggles">${STREAMER_TOGGLE_FIELDS.map(t => html`
                    <label class="streamer-toggle-inline${t.planningOnly ? ' planning-only' : ''}">
                        <input type="checkbox" data-streamer-field="${t.key}" ${cfg[t.key] ? 'checked' : ''}>
                        <span>${t.label}</span>
                    </label>`)}</div>
                <div class="streamer-entry-grid">
                    <div class="streamer-field">
                        <label>Rôle à mentionner au début du live</label>
                        ${raw(rolePickerHtml(mentionRole, 'data-streamer-field="twitchMentionRoleId"', 'mention'))}
                    </div>
                    <div class="streamer-field form-field streamer-template">
                        <label>Message au début du live</label>
                        <input type="text" class="form-input" data-streamer-field="twitchNotificationTemplate" data-template-text value="${template}" maxlength="500"
                               placeholder="{streamer} est en live : {titre} !" aria-label="Message au début du live">
                        <button type="button" class="btn btn-ghost btn-sm template-test-btn"
                                data-click="testTemplate" data-click-args="${actionArgs(state.currentServerId, 'moduleTwitch', 'twitchNotificationTemplate', EL)}"
                                title="Le bot t'envoie en MP ce que donne ce message">✉️ Tester en MP</button>
                        <div class="discord-preview" aria-live="polite" aria-label="Aperçu dans Discord"
                             data-server="${state.currentServerId}" data-module="moduleTwitch" data-preview-field="twitchNotificationTemplate"></div>
                    </div>
                </div>
            </div>`;
}

export const streamerMapField = {
    render: ({ key, value }) => {
        const streamers = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
        return html`
            <div class="streamers-list" data-field="${key}" data-type="streamermap">
                ${Object.entries(streamers).map(([login, cfg]) => renderStreamerEntry(login, cfg || {}))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addStreamerEntry" data-click-args="${actionArgs(EL)}">+ Ajouter un streamer</button>`;
    },
    collect: (list) => {
        const streamers = {};
        for (const entry of list.querySelectorAll('.streamer-entry')) {
            const field = (name) => entry.querySelector(`[data-streamer-field="${name}"]`);
            const login = field('login')?.value?.trim();
            if (!login) continue;
            const planningEnabled = !!entry.querySelector('[data-streamer-planning-enabled]')?.checked;
            const planningChan = channelPickerValue(field('twitchPlanningChannelId'));
            const notifChan = channelPickerValue(field('twitchNotificationChannelId'));
            const cfg = {};
            if (planningEnabled && planningChan) cfg.twitchPlanningChannelId = planningChan;
            if (notifChan) cfg.twitchNotificationChannelId = notifChan;
            for (const t of STREAMER_TOGGLE_FIELDS) {
                if (t.planningOnly && !planningEnabled) continue;
                if (field(t.key)?.checked) cfg[t.key] = true;
            }
            const mentionRoleId = field('twitchMentionRoleId')?.value?.trim();
            const template = field('twitchNotificationTemplate')?.value?.trim();
            if (mentionRoleId) cfg.twitchMentionRoleId = mentionRoleId;
            if (template) cfg.twitchNotificationTemplate = template;
            streamers[login] = cfg;
        }
        return streamers;
    },
};

export function toggleStreamerPlanning(checkbox) {
    const entry = checkbox.closest('.streamer-entry');
    if (!entry) return;
    entry.classList.toggle('planning-disabled', !checkbox.checked);
}

export function addStreamerEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('streamers-list')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = String(renderStreamerEntry('', {})).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    populateChannelPickers(entry);
    populateRolePickers(entry);
    entry.querySelector('input[type="text"]').focus();
}

registerActions({
    addStreamerEntry,
    toggleStreamerPlanning,
});
