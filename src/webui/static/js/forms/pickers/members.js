// The member picker, searching through the server.
import { registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { state } from '../../core/state.js';
import { escapeHtml } from '../../core/util.js';
import { refreshSpotifyMapListOptions } from '../widgets/spotify-users.js';

// ── Member picker ─────────────────────────────────
// A guild can have tens of thousands of members: the dashboard never loads
// them all. Each picker shows the first page of the guild's members, or the
// page its search box found (server-side, /members?q=), and the members a
// form already references are resolved by id (/members?ids=) to be labelled.
export const MEMBER_PAGE = 50;
export const MEMBER_IDS_PER_REQUEST = 200;
export const _memberCache = {}; // serverId -> { byId: Map(id -> member), firstPage, total }

export function memberStore(serverId) {
    if (!_memberCache[serverId]) {
        _memberCache[serverId] = { byId: new Map(), firstPage: null, total: 0 };
    }
    return _memberCache[serverId];
}

export async function queryServerMembers(serverId, params) {
    const qs = new URLSearchParams(params).toString();
    const data = await api(`/api/servers/${encodeURIComponent(serverId)}/members?${qs}`);
    if (!data || !Array.isArray(data.members)) return null;
    const store = memberStore(serverId);
    for (const m of data.members) store.byId.set(String(m.id), m);
    return data;
}

export async function firstMemberPage(serverId) {
    if (!serverId) return null;
    const store = memberStore(serverId);
    if (!store.firstPage) {
        const data = await queryServerMembers(serverId, { limit: MEMBER_PAGE });
        if (!data) return null;
        store.firstPage = data.members;
        store.total = data.total || data.members.length;
    }
    return store.firstPage;
}

// Look up the members behind *ids* not seen yet; returns the id -> member map.
export async function resolveServerMembers(serverId, ids) {
    const store = memberStore(serverId);
    const missing = [...new Set(ids.map(String).filter(id => /^\d+$/.test(id) && !store.byId.has(id)))];
    for (let i = 0; i < missing.length; i += MEMBER_IDS_PER_REQUEST) {
        await queryServerMembers(serverId, { ids: missing.slice(i, i + MEMBER_IDS_PER_REQUEST).join(',') });
    }
    return store.byId;
}

export function memberPickerHtml(currentId, fieldAttr) {
    const safeId = escapeHtml(currentId || '');
    return `
            <div class="member-picker" data-current="${safeId}">
                <input type="search" class="form-input member-picker-search" placeholder="🔍 Rechercher" maxlength="100" data-input="onMemberPickerSearch" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]" aria-label="Rechercher un membre">
                <select class="form-input member-picker-select" data-change="onMemberPickerChange" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <option value="">— Chargement des membres… —</option>
                    ${currentId ? `<option value="${safeId}" selected>${safeId}</option>` : ''}
                </select>
                <input type="text" class="form-input member-picker-manual" ${fieldAttr} value="${safeId}" placeholder="ex: 123456789012345678" style="display:none;">
                <button type="button" class="btn-member-toggle" data-click="toggleMemberPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
            </div>`;
}

export function getDiscord2NameMap() {
    const sid = state.currentServerId;
    const map = state.servers?.[sid]?.config?.discord2name;
    return (map && typeof map === 'object' && !Array.isArray(map)) ? map : {};
}

export function memberOptionLabel(m) {
    const prenom = getDiscord2NameMap()[m.id];
    const fallback = m.display_name || m.username || m.id;
    const display = prenom || fallback;
    const uname = m.username && m.username !== display ? ` (${m.username})` : '';
    return `${display}${uname}`;
}

export function getPickerSelectedId(picker) {
    const manual = picker.querySelector('.member-picker-manual');
    const select = picker.querySelector('.member-picker-select');
    const manualVisible = manual && manual.style.display !== 'none';
    const val = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
    return (val || picker.dataset.current || '').trim();
}

// The pickers whose choices exclude each other: the rows of one list.
export function memberPickerGroup(picker) {
    const list = picker.closest('.namemap-list, .spotifymap-list');
    return list ? list.querySelectorAll('.member-picker') : [picker];
}

export function refreshMemberPickerGroup(pickers) {
    const store = _memberCache[state.currentServerId];
    if (!store || !store.firstPage) return;
    const currentIds = new Map();
    for (const p of pickers) currentIds.set(p, getPickerSelectedId(p));
    for (const picker of pickers) {
        const select = picker.querySelector('select');
        if (!select) continue;
        const ownId = currentIds.get(picker) || '';
        const excludeIds = new Set();
        for (const p of pickers) {
            if (p === picker) continue;
            const id = currentIds.get(p);
            if (id) excludeIds.add(id);
        }
        const searching = Array.isArray(picker._results);
        const list = searching ? picker._results : store.firstPage;
        const total = searching ? picker._total : store.total;
        let opts = `<option value="">${searching && !list.length ? '— Aucun membre trouvé —' : '— Choisir un membre —'}</option>`;
        const shown = new Set();
        if (ownId) {
            const m = store.byId.get(ownId);
            opts += `<option value="${escapeHtml(ownId)}">${escapeHtml(m ? memberOptionLabel(m) : `ID inconnu: ${ownId}`)}</option>`;
            shown.add(ownId);
        }
        for (const m of list) {
            if (shown.has(m.id) || excludeIds.has(m.id)) continue;
            shown.add(m.id);
            opts += `<option value="${escapeHtml(m.id)}">${escapeHtml(memberOptionLabel(m))}</option>`;
        }
        if (total > list.length) {
            opts += `<option value="" disabled>… ${total - list.length} autre(s) : affine la recherche</option>`;
        }
        select.innerHTML = opts;
        select.value = ownId;
    }
}

export function refreshNameMapListOptions(list) {
    if (list) refreshMemberPickerGroup(list.querySelectorAll('.member-picker'));
}

export async function populateMemberPickers(container) {
    const serverId = state.currentServerId;
    if (!serverId || !container) return;
    const pickers = container.querySelectorAll('.member-picker');
    if (pickers.length === 0) return;
    let page = null;
    try {
        page = await firstMemberPage(serverId);
        if (page) await resolveServerMembers(serverId, [...pickers].map(getPickerSelectedId).filter(Boolean));
    } catch (e) {
        console.warn('Could not fetch members', e);
    }
    if (!page) {
        for (const picker of pickers) {
            toggleMemberPickerMode(picker.querySelector('.btn-member-toggle'), true);
            const select = picker.querySelector('select');
            if (select) select.innerHTML = `<option value="">— Membres indisponibles —</option>`;
        }
        return;
    }
    const groups = new Set();
    for (const picker of pickers) {
        const list = picker.closest('.namemap-list, .spotifymap-list');
        groups.add(list || picker);
    }
    for (const group of groups) {
        refreshMemberPickerGroup(group.classList.contains('member-picker') ? [group] : group.querySelectorAll('.member-picker'));
    }
}

export const _memberSearchTimers = new WeakMap();

export function onMemberPickerSearch(input) {
    const picker = input.closest('.member-picker');
    if (!picker) return;
    clearTimeout(_memberSearchTimers.get(picker));
    _memberSearchTimers.set(picker, setTimeout(() => runMemberPickerSearch(picker, input.value), 250));
}

export async function runMemberPickerSearch(picker, query) {
    const serverId = state.currentServerId;
    const seq = (picker._searchSeq || 0) + 1;
    picker._searchSeq = seq;
    const q = query.trim();
    let results = null, total = 0;
    if (q) {
        let data = null;
        try {
            data = await queryServerMembers(serverId, { q, limit: MEMBER_PAGE });
        } catch (e) {
            console.warn('Member search failed', e);
        }
        if (picker._searchSeq !== seq) return;  // a newer search took over
        results = data ? data.members : [];
        total = data ? (data.total || 0) : 0;
    }
    picker._results = results;
    picker._total = total;
    refreshMemberPickerGroup(memberPickerGroup(picker));
}

export function onMemberPickerChange(select) {
    const picker = select.closest('.member-picker');
    if (!picker) return;
    const manual = picker.querySelector('.member-picker-manual');
    if (manual) manual.value = select.value;
    // Auto-fill the adjacent name field if empty (namemap entries only)
    const namemapEntry = picker.closest('.namemap-entry');
    if (namemapEntry) {
        const nameInput = namemapEntry.querySelector('[data-namemap-field="name"]');
        if (nameInput && !nameInput.value.trim()) {
            const match = _memberCache[state.currentServerId]?.byId.get(select.value);
            if (match) nameInput.value = match.display_name || match.username || '';
        }
    }
    // Update sibling pickers so the newly-picked member is removed from their dropdowns
    const namemapList = picker.closest('.namemap-list');
    if (namemapList) refreshNameMapListOptions(namemapList);
    const spotifymapList = picker.closest('.spotifymap-list');
    if (spotifymapList) refreshSpotifyMapListOptions(spotifymapList);
}

export function removeNameMapEntry(btn) {
    const entry = btn.closest('.namemap-entry');
    if (!entry) return;
    const list = entry.closest('.namemap-list');
    entry.remove();
    if (list) refreshNameMapListOptions(list);
}

export function toggleMemberPickerMode(btn, forceManual) {
    const picker = btn.closest('.member-picker');
    if (!picker) return;
    const select = picker.querySelector('.member-picker-select');
    const manual = picker.querySelector('.member-picker-manual');
    const search = picker.querySelector('.member-picker-search');
    const showManual = forceManual === true ? true : (manual.style.display === 'none');
    manual.style.display = showManual ? '' : 'none';
    select.style.display = showManual ? 'none' : '';
    if (search) search.style.display = showManual ? 'none' : '';
    btn.textContent = showManual ? '☰' : '✎';
    btn.title = showManual ? 'Choisir dans la liste' : 'Saisie manuelle';
    const typed = manual.value.trim();
    if (showManual && select.value && !typed) {
        manual.value = select.value;
    } else if (!showManual && typed && typed !== select.value) {
        // The id typed by hand becomes the list's choice, labelled once resolved.
        if (!select.querySelector(`option[value="${CSS.escape(typed)}"]`)) {
            select.add(new Option(typed, typed), 1);
        }
        select.value = typed;
        const serverId = state.currentServerId;
        resolveServerMembers(serverId, [typed])
            .catch(e => console.warn('Could not resolve member', e))
            .finally(() => refreshMemberPickerGroup(memberPickerGroup(picker)));
    }
}

registerActions({
    onMemberPickerChange,
    onMemberPickerSearch,
    removeNameMapEntry,
    toggleMemberPickerMode,
});
