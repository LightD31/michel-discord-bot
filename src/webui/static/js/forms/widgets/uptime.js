// Uptime alerts (P42): each monitor of the server's Kuma, where its changes are
// announced, and how much the announcement says. The monitors come from the
// server's own Kuma (GET …/uptime/monitors), read with its saved key.
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { html } from '../../core/html.js';
import { state } from '../../core/state.js';
import { channelPickerHtml, channelPickerValue, populateChannelPickers } from '../pickers/channels.js';

const MODES = [['detailed', 'Détaillé'], ['simple', 'Simple']];
const _monitors = {};  // serverId -> Promise<{monitors} | {error}>

export function renderAlertEntry(a, idx) {
    a = a || {};
    const id = String(a.monitorId || '');
    return html`
            <div class="team-entry uptime-alert" data-alert-idx="${idx}">
                <div class="team-entry-grid">
                    <div class="team-field">
                        <label>Capteur</label>
                        <select class="uptime-monitor-select" data-alert-field="monitorId" data-current="${id}" aria-label="Capteur">
                            <option value="${id}">${id ? `ID ${id}` : '— Chargement des capteurs… —'}</option>
                        </select>
                    </div>
                    <div class="team-field team-field-channel">
                        <label>Salon des annonces</label>
                        ${channelPickerHtml(html`data-alert-field="channelId"`, a.channelId)}
                    </div>
                    <div class="team-field">
                        <label>Annonces</label>
                        <select data-alert-field="mode" aria-label="Détail des annonces">
                            ${MODES.map(([v, label]) => html`<option value="${v}" ${(a.mode || 'detailed') === v ? 'selected' : ''}>${label}</option>`)}
                        </select>
                    </div>
                </div>
                <div class="team-entry-footer">
                    <span></span>
                    <button class="btn-remove-team" data-click="remove-closest" data-click-args="${actionArgs('.uptime-alert', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
            </div>`;
}

export const uptimeAlertsField = {
    render: ({ key, value }) => html`
            <div class="teams-list" data-field="${key}" data-type="uptimealerts">
                ${(Array.isArray(value) ? value : []).map((a, idx) => renderAlertEntry(a, idx))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addUptimeAlert" data-click-args="${actionArgs(EL)}">+ Ajouter une alerte</button>`,
    collect: (list) => {
        const alerts = [];
        for (const entry of list.querySelectorAll('.uptime-alert')) {
            const monitorId = entry.querySelector('[data-alert-field="monitorId"]')?.value?.trim();
            const channelId = channelPickerValue(entry.querySelector('.channel-picker[data-alert-field="channelId"]'));
            if (!monitorId || !channelId) continue;
            const mode = entry.querySelector('[data-alert-field="mode"]')?.value || 'detailed';
            alerts.push({ monitorId, channelId, mode });
        }
        return alerts;
    },
};

function monitorsOf(serverId) {
    _monitors[serverId] ||= api(`/api/servers/${encodeURIComponent(serverId)}/uptime/monitors`, { quiet: true })
        .catch(e => ({ error: String(e?.message || e) }));
    return _monitors[serverId];
}

// Fill each monitor picker under *root* from the server's Kuma, keeping its choice.
export async function populateUptimeMonitors(root) {
    const selects = [...root.querySelectorAll('select.uptime-monitor-select')];
    if (!selects.length || !state.currentServerId) return;
    const data = await monitorsOf(state.currentServerId);
    for (const select of selects) {
        const current = select.dataset.current || select.value;
        const monitors = data?.monitors || [];
        const known = monitors.some(m => m.id === current);
        select.innerHTML = String(html`
            ${!current && html`<option value="">${data?.error ? '— Capteurs indisponibles —' : '— Choisir un capteur —'}</option>`}
            ${current && !known && html`<option value="${current}">ID ${current}${data?.error ? '' : ' (introuvable dans Kuma)'}</option>`}
            ${monitors.map(m => html`<option value="${m.id}">${m.name}</option>`)}`);
        select.value = current;
        if (data?.error) select.title = data.error;
    }
}

export function addUptimeAlert(btn) {
    const list = btn.previousElementSibling;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = String(renderAlertEntry({}, list.querySelectorAll('.uptime-alert').length)).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    populateChannelPickers(entry);
    populateUptimeMonitors(entry);
}

registerActions({
    addUptimeAlert,
});
