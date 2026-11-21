// The YouTube channel list.
import { registerActions } from '../../core/actions.js';
import { escapeHtml } from '../../core/util.js';

export function renderYoutubeChannelEntry(handle, cfg) {
    const shorts = !!cfg.shorts;
    const live = !!cfg.live;
    const vod = cfg.vod !== false; // default true
    return `
            <div class="youtubechannel-entry">
                <input type="text" data-yt-field="handle" value="${escapeHtml(handle || '')}" placeholder="@handle">
                <input type="text" data-yt-field="label" value="${escapeHtml(cfg.label || '')}" placeholder="Libellé (optionnel)">
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="shorts" ${shorts ? 'checked' : ''}>
                    <span>Shorts</span>
                </label>
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="live" ${live ? 'checked' : ''}>
                    <span>Live</span>
                </label>
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="vod" ${vod ? 'checked' : ''}>
                    <span>VOD</span>
                </label>
                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.youtubechannel-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
            </div>`;
}

export function addYoutubeChannelEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('youtubechannel-list')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = renderYoutubeChannelEntry('', { shorts: false, live: false, vod: true }).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    entry.querySelector('input[type="text"]').focus();
}

registerActions({
    addYoutubeChannelEntry,
});
