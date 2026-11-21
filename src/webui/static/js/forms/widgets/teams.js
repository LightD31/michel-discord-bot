// The esport team list.
import { registerActions } from '../../core/actions.js';
import { escapeHtml } from '../../core/util.js';
import { populateChannelPickers } from '../pickers/channels.js';

export function renderTeamEntry(t, idx) {
    t = t || {};
    const chan = String(t.channelId || '').trim();
    const pin = !!t.pin;
    return `
            <div class="team-entry" data-team-idx="${idx}">
                <div class="team-entry-grid">
                    <div class="team-field">
                        <label>Nom</label>
                        <input type="text" data-team-field="name" value="${escapeHtml(t.name || '')}" placeholder="ex: Mandatory">
                    </div>
                    <div class="team-field">
                        <label>ID VLR.gg</label>
                        <input type="text" data-team-field="vlrTeamId" value="${escapeHtml(t.vlrTeamId || '')}" placeholder="ex: 7967">
                    </div>
                    <div class="team-field team-field-channel">
                        <label>Salon planning</label>
                        <div class="channel-picker" data-team-field="channelId" data-current="${escapeHtml(chan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${chan ? `<option value="${escapeHtml(chan)}" selected>${escapeHtml(chan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(chan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                        </div>
                    </div>
                </div>
                <div class="team-entry-footer">
                    <label class="streamer-toggle-inline">
                        <input type="checkbox" data-team-field="pin" ${pin ? 'checked' : ''}>
                        <span>Épingler le message</span>
                    </label>
                    <button class="btn-remove-team" data-click="remove-closest" data-click-args="[&quot;.team-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
            </div>`;
}

export function addTeamEntry(btn) {
    const list = btn.previousElementSibling;
    const idx = list.querySelectorAll('.team-entry').length;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = renderTeamEntry({}, idx).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    populateChannelPickers(entry);
    entry.querySelector('input[data-team-field="name"]').focus();
}

registerActions({
    addTeamEntry,
});
