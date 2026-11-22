// The Twitch streamer list.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html } from '../../core/html.js';
import { state } from '../../core/state.js';
import { escapeHtml } from '../../core/util.js';
import { populateChannelPickers } from '../pickers/channels.js';
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
    const toggles = STREAMER_TOGGLE_FIELDS.map(t => `
            <label class="streamer-toggle-inline${t.planningOnly ? ' planning-only' : ''}">
                <input type="checkbox" data-streamer-field="${t.key}" ${cfg[t.key] ? 'checked' : ''}>
                <span>${escapeHtml(t.label)}</span>
            </label>`).join('');
    return `
            <div class="streamer-entry${planningEnabled ? '' : ' planning-disabled'}">
                <div class="streamer-entry-header">
                    <input type="text" data-streamer-field="login" value="${escapeHtml(login || '')}" placeholder="Login Twitch (ex: zerator)">
                    <button class="btn-remove-streamer" data-click="remove-closest" data-click-args="[&quot;.streamer-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
                <label class="streamer-planning-toggle">
                    <input type="checkbox" data-streamer-planning-enabled ${planningEnabled ? 'checked' : ''} data-change="toggleStreamerPlanning" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <span>Activer le message de planning</span>
                </label>
                <div class="streamer-entry-grid">
                    <div class="streamer-field planning-only">
                        <label>Salon planning</label>
                        <div class="channel-picker" data-streamer-field="twitchPlanningChannelId" data-current="${escapeHtml(planningChan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${planningChan ? `<option value="${escapeHtml(planningChan)}" selected>${escapeHtml(planningChan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(planningChan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                        </div>
                    </div>
                    <div class="streamer-field">
                        <label>Salon notifications</label>
                        <div class="channel-picker" data-streamer-field="twitchNotificationChannelId" data-current="${escapeHtml(notifChan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${notifChan ? `<option value="${escapeHtml(notifChan)}" selected>${escapeHtml(notifChan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(notifChan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                        </div>
                    </div>
                </div>
                <div class="streamer-toggles">${toggles}</div>
                <div class="streamer-entry-grid">
                    <div class="streamer-field">
                        <label>Rôle à mentionner au début du live</label>
                        ${rolePickerHtml(mentionRole, 'data-streamer-field="twitchMentionRoleId"', 'mention')}
                    </div>
                    <div class="streamer-field form-field streamer-template">
                        <label>Message au début du live</label>
                        ${html`<input type="text" class="form-input" data-streamer-field="twitchNotificationTemplate" data-template-text value="${template}" maxlength="500"
                               placeholder="{streamer} est en live : {titre} !" aria-label="Message au début du live">
                        <button type="button" class="btn btn-ghost btn-sm template-test-btn"
                                data-click="testTemplate" data-click-args="${actionArgs(state.currentServerId, 'moduleTwitch', 'twitchNotificationTemplate', EL)}"
                                title="Le bot t'envoie en MP ce que donne ce message">✉️ Tester en MP</button>
                        <div class="discord-preview" aria-live="polite" aria-label="Aperçu dans Discord"
                             data-server="${state.currentServerId}" data-module="moduleTwitch" data-preview-field="twitchNotificationTemplate"></div>`}
                    </div>
                </div>
            </div>`;
}

export function toggleStreamerPlanning(checkbox) {
    const entry = checkbox.closest('.streamer-entry');
    if (!entry) return;
    entry.classList.toggle('planning-disabled', !checkbox.checked);
}

export function addStreamerEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('streamers-list')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = renderStreamerEntry('', {}).trim();
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
