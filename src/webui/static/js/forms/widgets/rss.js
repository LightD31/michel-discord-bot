// The RSS feed list and its presets.
import { registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { escapeHtml } from '../../core/util.js';
import { toast } from '../../ui/toast.js';
import { populateChannelPickers } from '../pickers/channels.js';

export function renderRssFeedEntry(feedId, cfg) {
    const safeChannel = escapeHtml(cfg.channel_id || '');
    const mode = (cfg.mode === 'embed') ? 'embed' : 'text';
    // Discord brand-blue default; #RRGGBB format expected by <input type="color">.
    let color = (cfg.embed_color || '#5865F2').toString().trim();
    if (!color.startsWith('#')) color = '#' + color;
    if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = '#5865F2';
    const showImage = cfg.embed_show_image !== false;
    const showLinkButton = !!cfg.embed_show_link_button;
    // Feeds that came from a known preset render locked/compact by default so
    // the curated embed templates can't be broken by accident. Advanced editing
    // stays one click away via the toggle.
    const isPreset = !!(typeof RSS_PRESETS !== 'undefined' && RSS_PRESETS[feedId]);
    const advanced = isPreset ? 'off' : 'on';
    const advancedLabel = isPreset ? '⚙️ Édition avancée' : '▾ Réduire';
    const presetName = escapeHtml(cfg.label || feedId || '');
    const banner = isPreset ? `
                <div class="rssfeed-preset-banner">
                    <span class="rssfeed-preset-badge">🎮 Préréglage</span>
                    <span class="rssfeed-preset-name">${presetName}</span>
                    <span class="rssfeed-preset-hint">Mise en forme gérée automatiquement — choisis le salon ci-dessous.</span>
                </div>` : '';
    return `
            <div class="rssfeed-entry${isPreset ? ' preset' : ''}" data-mode="${mode}" data-advanced="${advanced}">${banner}
                <div class="rssfeed-row">
                    <input class="rssfeed-identity" type="text" data-rss-field="feed_id" value="${escapeHtml(feedId || '')}" placeholder="ex: epic-free">
                    <input class="rssfeed-identity" type="text" data-rss-field="url" value="${escapeHtml(cfg.url || '')}" placeholder="https://exemple.com/feed.xml">
                    <input class="rssfeed-identity" type="text" data-rss-field="label" value="${escapeHtml(cfg.label || '')}" placeholder="Libellé (optionnel)">
                    <div class="channel-picker" data-rss-field="channel_id" data-current="${safeChannel}">
                        <select class="form-input channel-picker-select">
                            <option value="">— Salon par défaut —</option>
                            ${cfg.channel_id ? `<option value="${safeChannel}" selected>${safeChannel}</option>` : ''}
                        </select>
                        <input type="text" class="form-input channel-picker-manual" value="${safeChannel}" placeholder="ID de salon" style="display:none;">
                        <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle" aria-label="Saisie manuelle">✎</button>
                    </div>
                    <select class="rssfeed-identity" data-rss-field="mode" data-change="onRssModeChange" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                        <option value="text" ${mode === 'text' ? 'selected' : ''}>Texte</option>
                        <option value="embed" ${mode === 'embed' ? 'selected' : ''}>Embed</option>
                    </select>
                    <button type="button" class="btn-rss-advanced" data-click="toggleRssAdvanced" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Afficher / masquer l'éditeur complet">${advancedLabel}</button>
                    <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.rssfeed-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
                <div class="rssfeed-advanced">
                    <div class="rssfeed-template-row">
                        <span class="rssfeed-field-label">Modèle texte</span>
                        <textarea data-rss-field="template" rows="2" placeholder="ex: **{label}** — [{title}]({link})">${escapeHtml(cfg.template || '')}</textarea>
                    </div>
                    <div class="rssfeed-embed-panel">
                        <span class="rssfeed-field-label">Couleur</span>
                        <input type="color" data-rss-field="embed_color" value="${color}">
                        <span class="rssfeed-field-label">Image</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_image" ${showImage ? 'checked' : ''}>
                            <span>Afficher l'image détectée</span>
                        </label>
                        <span class="rssfeed-field-label">Titre embed</span>
                        <input type="text" data-rss-field="embed_title" value="${escapeHtml(cfg.embed_title || '')}" placeholder="{title}">
                        <span class="rssfeed-field-label">Description</span>
                        <textarea data-rss-field="embed_description" rows="4" placeholder="{summary}">${escapeHtml(cfg.embed_description || '')}</textarea>
                        <span class="rssfeed-field-label">Bouton lien</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_link_button" ${showLinkButton ? 'checked' : ''}>
                            <span>Afficher un bouton vers l'article</span>
                        </label>
                        <span class="rssfeed-field-label">Libellé bouton</span>
                        <input type="text" data-rss-field="embed_link_button_label" value="${escapeHtml(cfg.embed_link_button_label || '')}" placeholder="Voir l'article">
                    </div>
                    <div class="messagelist-variables" style="margin-top:8px;">
                        <span>Variables : </span>
                        ${['{title}','{link}','{summary}','{label}','{author}','{image}','{published_date}'].map(v =>
                        `<button type="button" class="var-chip" data-var="${v}" data-mousedown="insertVariable" data-mousedown-args="[{&quot;$&quot;:&quot;event&quot;},{&quot;$&quot;:&quot;el&quot;}]">${v}</button>`
                    ).join('')}
                    </div>
                </div>
            </div>`;
}

export function onRssModeChange(select) {
    const entry = select.closest('.rssfeed-entry');
    if (entry) entry.dataset.mode = select.value;
}

export function toggleRssAdvanced(btn) {
    const entry = btn.closest('.rssfeed-entry');
    if (!entry) return;
    const on = entry.dataset.advanced === 'on';
    entry.dataset.advanced = on ? 'off' : 'on';
    btn.textContent = on ? '⚙️ Édition avancée' : '▾ Réduire';
}

export function addRssFeedEntry(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('rssfeed-list')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = renderRssFeedEntry('', {}).trim();
    const entry = wrapper.firstElementChild;
    list.appendChild(entry);
    if (typeof populateChannelPickers === 'function') populateChannelPickers(entry);
    entry.querySelector('input[type="text"]').focus();
}

// Shared default templates for the LootScraper feeds. Description uses
// {summary} flattened by the parser plus a Discord timestamp tag for the
// publish time, and the link goes onto a Discord link button rather than
// being embedded inline.
// The feeds' base URL is config (global section "rss"), fetched on first use.
export const _LOOTSCRAPER_DESC = '{summary}\n\n🕐 Publié {published_relative} ({published_short})';
export let _lootScraperBase = null;
export async function lootScraperBase() {
    if (_lootScraperBase === null) {
        const data = await api('/api/presets/rss');
        _lootScraperBase = (data && data.lootScraperBaseUrl) || '';
    }
    return _lootScraperBase;
}
export function _lsPreset(slug, file, label, color, btnLabel, opts = {}) {
    return {
        feed_id: slug,
        file,
        label,
        mode: 'embed',
        embed_color: color,
        embed_title: (opts.titleEmoji || '🎮') + ' {title}',
        embed_description: _LOOTSCRAPER_DESC,
        embed_show_image: true,
        embed_show_link_button: true,
        embed_link_button_label: btnLabel,
    };
}
export const RSS_PRESETS = {
    // PC stores — free full games
    'lootscraper-epic': _lsPreset(
        'lootscraper-epic', 'lootscraper_epic_game.xml',
        'Epic Games — Gratuit', '#0078F2', 'Récupérer sur Epic Games'),
    'lootscraper-gog': _lsPreset(
        'lootscraper-gog', 'lootscraper_gog_game.xml',
        'GOG — Gratuit', '#86328A', 'Récupérer sur GOG'),
    'lootscraper-humble': _lsPreset(
        'lootscraper-humble', 'lootscraper_humble_game.xml',
        'Humble — Gratuit', '#CC2929', 'Récupérer sur Humble'),
    'lootscraper-itch': _lsPreset(
        'lootscraper-itch', 'lootscraper_itch_game.xml',
        'itch.io — Gratuit', '#FA5C5C', 'Récupérer sur itch.io'),
    // Subscription / loot — Amazon & Steam
    'lootscraper-amazon': _lsPreset(
        'lootscraper-amazon', 'lootscraper_amazon_game.xml',
        'Amazon Prime Gaming', '#FF9900', 'Récupérer sur Prime Gaming'),
    'lootscraper-amazon-loot': _lsPreset(
        'lootscraper-amazon-loot', 'lootscraper_amazon_loot.xml',
        'Amazon Prime — Loot', '#FF9900', 'Récupérer sur Prime Gaming',
        { titleEmoji: '🎁' }),
    'lootscraper-steam': _lsPreset(
        'lootscraper-steam', 'lootscraper_steam_game.xml',
        'Steam — Gratuit', '#66C0F4', 'Récupérer sur Steam'),
    'lootscraper-steam-loot': _lsPreset(
        'lootscraper-steam-loot', 'lootscraper_steam_loot.xml',
        'Steam — Loot', '#66C0F4', 'Récupérer sur Steam',
        { titleEmoji: '🎁' }),
    // Mobile stores
    'lootscraper-epic-android': _lsPreset(
        'lootscraper-epic-android', 'lootscraper_epic_game_android.xml',
        'Epic Games — Android', '#0078F2', 'Récupérer sur Epic Games',
        { titleEmoji: '📱' }),
    'lootscraper-epic-ios': _lsPreset(
        'lootscraper-epic-ios', 'lootscraper_epic_game_ios.xml',
        'Epic Games — iOS', '#0078F2', 'Récupérer sur Epic Games',
        { titleEmoji: '📱' }),
    'lootscraper-apple': _lsPreset(
        'lootscraper-apple', 'lootscraper_apple_game.xml',
        'App Store — Gratuit', '#007AFF', "Récupérer sur l'App Store",
        { titleEmoji: '📱' }),
    'lootscraper-google': _lsPreset(
        'lootscraper-google', 'lootscraper_google_game.xml',
        'Google Play — Gratuit', '#34A853', 'Récupérer sur Google Play',
        { titleEmoji: '📱' }),
};
// "All" bundle keeps the four originals (Epic / Amazon / itch / Steam) so
// a fresh install matches the canonical free-PC-games setup.
export const _LOOTSCRAPER_ALL_PC_GAMES = [
    'lootscraper-epic', 'lootscraper-gog',
    'lootscraper-humble', 'lootscraper-itch',
    'lootscraper-amazon', 'lootscraper-steam',
];
export const _LOOTSCRAPER_ALL_BASIC = [
    'lootscraper-epic', 'lootscraper-amazon',
    'lootscraper-itch', 'lootscraper-steam',
];

export async function applyRssPreset(select) {
    const value = select.value;
    select.value = '';
    if (!value) return;
    let base = '';
    try {
        base = await lootScraperBase();
    } catch (e) {
        return;  // api() already showed the error
    }
    if (!base) {
        toast("Préréglages indisponibles : renseigne l'URL des flux LootScraper dans la section globale « Flux RSS ».", 'error');
        return;
    }
    if (!base.endsWith('/')) base += '/';
    let list = select.previousElementSibling;
    while (list && !(list.classList && list.classList.contains('rssfeed-list'))) {
        list = list.previousElementSibling;
    }
    if (!list) return;
    let ids;
    if (value === 'lootscraper-all') ids = _LOOTSCRAPER_ALL_BASIC;
    else if (value === 'lootscraper-all-pc') ids = _LOOTSCRAPER_ALL_PC_GAMES;
    else if (value === 'lootscraper-everything') ids = Object.keys(RSS_PRESETS);
    else ids = [value];
    const existing = new Set(
        Array.from(list.querySelectorAll('.rssfeed-entry input[data-rss-field="feed_id"]'))
            .map(i => (i.value || '').trim())
            .filter(Boolean)
    );
    for (const id of ids) {
        const preset = RSS_PRESETS[id];
        if (!preset || existing.has(preset.feed_id)) continue;
        const wrapper = document.createElement('div');
        const { file, ...cfg } = preset;
        wrapper.innerHTML = renderRssFeedEntry(preset.feed_id, { ...cfg, url: base + file }).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        if (typeof populateChannelPickers === 'function') populateChannelPickers(entry);
        existing.add(preset.feed_id);
    }
}

registerActions({
    addRssFeedEntry,
    applyRssPreset,
    onRssModeChange,
    toggleRssAdvanced,
});
