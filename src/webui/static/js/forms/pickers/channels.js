// The channel picker.
import { api } from '../../core/api.js';
import { state } from '../../core/state.js';
import { EL, actionArgs, registerActions } from '../../core/actions.js';
import { html, raw } from '../../core/html.js';

// ── Channel picker ────────────────────────────────
export const _channelCache = {}; // serverId -> {channels, categories}

export async function fetchServerChannels(serverId) {
    if (!serverId) return null;
    if (_channelCache[serverId]) return _channelCache[serverId];
    try {
        const data = await api(`/api/servers/${serverId}/channels`);
        if (data && data.channels) {
            _channelCache[serverId] = data;
            return data;
        }
    } catch (e) {
        console.warn('Could not fetch channels', e);
    }
    return null;
}

// The <option>s of a guild's channels, by category, each thread under its
// channel; *exclude* holds ids to leave out (those a list already has).
// *forums*: the server's forums alone (a forum field), else every channel but them.
export function channelOptionsHtml(data, exclude, forums = false) {
    const {categories} = data;
    const channels = data.channels.filter(c => Boolean(c.is_forum) === forums);
    const skip = exclude || new Set();
    // Split top-level channels from threads and index threads by their parent text channel
    const topLevel = [];
    const threadsByParent = new Map();
    for (const c of channels) {
        if (skip.has(String(c.id))) continue;
        if (c.is_thread) {
            const pid = c.parent_id || '__orphan__';
            if (!threadsByParent.has(pid)) threadsByParent.set(pid, []);
            threadsByParent.get(pid).push(c);
        } else {
            topLevel.push(c);
        }
    }
    // Group top-level channels by parent category
    const grouped = new Map();
    for (const c of topLevel) {
        const catId = c.parent_id || '__none__';
        if (!grouped.has(catId)) grouped.set(catId, []);
        grouped.get(catId).push(c);
    }
    // Build the options HTML once
    let opts = '';
    const orderedCats = [
        ['__none__', 'Sans catégorie'],
        ...(categories || []).map(c => [c.id, c.name]),
    ];
    const renderThreadOption = (t) => {
        const prefix = t.archived ? '🗄️ ' : '🧵 ';
        return String(html`<option value="${t.id}">&nbsp;&nbsp;↳ ${raw(prefix)}${t.name}</option>`);
    };
    for (const [catId, catName] of orderedCats) {
        const list = grouped.get(catId);
        if (!list || list.length === 0) continue;
        opts += String(html`<optgroup label="${catName}">`);
        for (const ch of list) {
            opts += String(html`<option value="${ch.id}">#${ch.name}</option>`);
            const threads = threadsByParent.get(ch.id);
            if (threads && threads.length) {
                for (const t of threads) {
                    opts += renderThreadOption(t);
                }
                threadsByParent.delete(ch.id);
            }
        }
        opts += '</optgroup>';
    }
    // Any threads whose parent isn't in the visible channel list (e.g. no perms on parent)
    const orphanThreads = [];
    for (const list of threadsByParent.values()) {
        for (const t of list) orphanThreads.push(t);
    }
    if (orphanThreads.length) {
        opts += `<optgroup label="Fils (autres)">`;
        for (const t of orphanThreads) {
            const prefix = t.archived ? '🗄️ ' : '🧵 ';
            opts += String(html`<option value="${t.id}">${raw(prefix)}${t.name}</option>`);
        }
        opts += '</optgroup>';
    }
    return opts;
}

// *serverId*: the server whose channels to list, the current one by default
// (the global settings pass the development server's: they have no current one).
export async function populateChannelPickers(container, serverId = state.currentServerId, missing = '') {
    if (!container) return;
    const pickers = container.querySelectorAll('.channel-picker');
    if (pickers.length === 0) return;
    const data = serverId ? await fetchServerChannels(serverId) : null;
    if (!serverId && !missing) return;
    if (!data) {
        // Fall back to manual text entry
        for (const picker of pickers) {
            toggleChannelPickerMode(picker.querySelector('.btn-channel-toggle'), true);
            const select = picker.querySelector('select');
            if (select) select.innerHTML = String(html`<option value="">${serverId ? '— Bot hors ligne —' : missing}</option>`);
        }
        return;
    }
    const opts = '<option value="">— Choisir un salon —</option>' + channelOptionsHtml(data);
    const forumOpts = '<option value="">— Choisir un forum —</option>' + channelOptionsHtml(data, null, true);
    const channels = data.channels;
    const validIds = new Set(channels.map(c => c.id));
    for (const picker of pickers) {
        const select = picker.querySelector('select');
        const manual = picker.querySelector('.channel-picker-manual');
        const current = picker.dataset.current || '';
        select.innerHTML = picker.dataset.type === 'forum' ? forumOpts : opts;
        if (current && validIds.has(current)) {
            select.value = current;
        } else if (current) {
            // Keep the unknown value visible at the top
            const opt = document.createElement('option');
            opt.value = current;
            opt.textContent = `ID inconnu: ${current}`;
            opt.selected = true;
            select.insertBefore(opt, select.firstChild.nextSibling);
        }
        // Sync manual value with select on change
        select.addEventListener('change', () => {
            if (manual) manual.value = select.value;
        });
    }
}

export function toggleChannelPickerMode(btn, forceManual) {
    const picker = btn.closest('.channel-picker');
    if (!picker) return;
    const select = picker.querySelector('.channel-picker-select');
    const manual = picker.querySelector('.channel-picker-manual');
    const showManual = forceManual === true ? true : manual.hidden;
    manual.hidden = !showManual;
    select.hidden = showManual;
    btn.textContent = showManual ? '☰' : '✎';
    btn.title = showManual ? 'Choisir dans la liste' : 'Saisie manuelle';
    if (showManual && select.value && !manual.value) {
        manual.value = select.value;
    } else if (!showManual && manual.value && !select.value) {
        // Try to set select from manual
        const opt = select.querySelector(`option[value="${CSS.escape(manual.value)}"]`);
        if (opt) select.value = manual.value;
    }
}

registerActions({
    toggleChannelPickerMode,
});

// The id a channel picker holds: its list's choice, or the id typed by hand.
export function channelPickerValue(picker) {
    if (!picker) return '';
    if (picker.tagName === 'INPUT') return (picker.value || '').trim();
    const manual = picker.querySelector('.channel-picker-manual');
    const select = picker.querySelector('.channel-picker-select');
    const manualVisible = manual && !manual.hidden;
    return ((manualVisible ? manual?.value : select?.value) ?? '').trim();
}

// A channel picker: the list (filled by populateChannelPickers), the id typed by
// hand behind ✎. *attrs* names it in its form (data-field, data-team-field…).
export function channelPickerHtml(attrs, current = '', { type = '', empty = '— Chargement des salons… —' } = {}) {
    const id = String(current || '').trim();
    return html`<div class="channel-picker" ${attrs} ${type && html`data-type="${type}"`} data-current="${id}">
            <select class="form-input channel-picker-select">
                <option value="">${empty}</option>
                ${id && html`<option value="${id}" selected>${id}</option>`}
            </select>
            <input type="text" class="form-input channel-picker-manual" value="${id}" placeholder="ID du salon" hidden>
            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="${actionArgs(EL)}" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
        </div>`;
}
