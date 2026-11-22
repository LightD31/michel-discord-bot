// The RSS feed list and its presets.
import { EL, EVENT, actionArgs, registerActions } from '../../core/actions.js';
import { api } from '../../core/api.js';
import { html } from '../../core/html.js';
import { toast } from '../../ui/toast.js';
import { channelPickerHtml, channelPickerValue, populateChannelPickers } from '../pickers/channels.js';

const RSS_VARIABLES = ['{title}', '{link}', '{summary}', '{label}', '{author}', '{image}', '{published_date}'];

export function renderRssFeedEntry(feedId, cfg) {
    const mode = (cfg.mode === 'embed') ? 'embed' : 'text';
    // Discord brand-blue default; #RRGGBB format expected by <input type="color">.
    let color = (cfg.embed_color || '#5865F2').toString().trim();
    if (!color.startsWith('#')) color = '#' + color;
    if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = '#5865F2';
    // Feeds that came from a known preset render locked/compact by default so
    // the curated embed templates can't be broken by accident. Advanced editing
    // stays one click away via the toggle.
    const isPreset = !!(typeof RSS_PRESETS !== 'undefined' && RSS_PRESETS[feedId]);
    return html`
            <div class="rssfeed-entry${isPreset ? ' preset' : ''}" data-mode="${mode}" data-advanced="${isPreset ? 'off' : 'on'}">
                ${isPreset && html`
                <div class="rssfeed-preset-banner">
                    <span class="rssfeed-preset-badge">🎮 Préréglage</span>
                    <span class="rssfeed-preset-name">${cfg.label || feedId || ''}</span>
                    <span class="rssfeed-preset-hint">Mise en forme gérée automatiquement — choisis le salon ci-dessous.</span>
                </div>`}
                <div class="rssfeed-row">
                    <input class="rssfeed-identity" type="text" data-rss-field="feed_id" value="${feedId || ''}" placeholder="ex: epic-free">
                    <input class="rssfeed-identity" type="text" data-rss-field="url" value="${cfg.url || ''}" placeholder="https://exemple.com/feed.xml">
                    <input class="rssfeed-identity" type="text" data-rss-field="label" value="${cfg.label || ''}" placeholder="Libellé (optionnel)">
                    ${channelPickerHtml(html`data-rss-field="channel_id"`, cfg.channel_id, { empty: '— Salon par défaut —' })}
                    <select class="rssfeed-identity" data-rss-field="mode" data-change="onRssModeChange" data-change-args="${actionArgs(EL)}">
                        <option value="text" ${mode === 'text' ? 'selected' : ''}>Texte</option>
                        <option value="embed" ${mode === 'embed' ? 'selected' : ''}>Embed</option>
                    </select>
                    <button type="button" class="btn-rss-advanced" data-click="toggleRssAdvanced" data-click-args="${actionArgs(EL)}" title="Afficher / masquer l'éditeur complet">${isPreset ? '⚙️ Édition avancée' : '▾ Réduire'}</button>
                    <button type="button" class="btn-rss-advanced" data-click="testRssFeed" data-click-args="${actionArgs(EL)}" title="Lire le flux maintenant et voir ses derniers articles comme Michel les posterait">🧪 Tester</button>
                    <button class="btn-remove-entry" data-click="remove-closest" data-click-args="${actionArgs('.rssfeed-entry', EL)}" title="Supprimer" aria-label="Supprimer">✕</button>
                </div>
                <div class="rssfeed-advanced">
                    <div class="rssfeed-template-row">
                        <span class="rssfeed-field-label">Modèle texte</span>
                        <textarea data-rss-field="template" rows="2" placeholder="ex: **{label}** — [{title}]({link})">${cfg.template || ''}</textarea>
                    </div>
                    <div class="rssfeed-embed-panel">
                        <span class="rssfeed-field-label">Couleur</span>
                        <input type="color" data-rss-field="embed_color" value="${color}">
                        <span class="rssfeed-field-label">Image</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_image" ${cfg.embed_show_image !== false ? 'checked' : ''}>
                            <span>Afficher l'image détectée</span>
                        </label>
                        <span class="rssfeed-field-label">Titre embed</span>
                        <input type="text" data-rss-field="embed_title" value="${cfg.embed_title || ''}" placeholder="{title}">
                        <span class="rssfeed-field-label">Description</span>
                        <textarea data-rss-field="embed_description" rows="4" placeholder="{summary}">${cfg.embed_description || ''}</textarea>
                        <span class="rssfeed-field-label">Bouton lien</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_link_button" ${cfg.embed_show_link_button ? 'checked' : ''}>
                            <span>Afficher un bouton vers l'article</span>
                        </label>
                        <span class="rssfeed-field-label">Libellé bouton</span>
                        <input type="text" data-rss-field="embed_link_button_label" value="${cfg.embed_link_button_label || ''}" placeholder="Voir l'article">
                    </div>
                    <div class="messagelist-variables rssfeed-variables">
                        <span>Variables : </span>
                        ${RSS_VARIABLES.map(v => html`<button type="button" class="var-chip" data-var="${v}" data-mousedown="insertVariable" data-mousedown-args="${actionArgs(EVENT, EL)}">${v}</button>`)}
                    </div>
                </div>
            </div>`;
}

// The presets offered under the list (the feeds are listed in RSS_PRESETS below).
const PRESET_GROUPS = [
    ['LootScraper — Bundles', [
        ['lootscraper-all', 'Bundle classique (Epic + Amazon + itch.io + Steam)'],
        ['lootscraper-all-pc', 'Bundle PC (Epic + GOG + Humble + itch.io + Amazon + Steam)'],
        ['lootscraper-everything', 'Tout LootScraper (12 flux)'],
    ]],
    ['LootScraper — Jeux PC gratuits', [
        ['lootscraper-epic', 'Epic Games'], ['lootscraper-gog', 'GOG'],
        ['lootscraper-humble', 'Humble'], ['lootscraper-itch', 'itch.io'],
    ]],
    ['LootScraper — Amazon & Steam', [
        ['lootscraper-amazon', 'Amazon Prime Gaming (jeux)'], ['lootscraper-amazon-loot', 'Amazon Prime Gaming (loot)'],
        ['lootscraper-steam', 'Steam (jeux gratuits)'], ['lootscraper-steam-loot', 'Steam (loot)'],
    ]],
    ['LootScraper — Mobile', [
        ['lootscraper-epic-android', 'Epic Games — Android'], ['lootscraper-epic-ios', 'Epic Games — iOS'],
        ['lootscraper-apple', 'App Store (iOS)'], ['lootscraper-google', 'Google Play (Android)'],
    ]],
];

export const rssFeedMapField = {
    render: ({ key, value }) => {
        const feeds = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
        return html`
            <div class="rssfeed-list" data-field="${key}" data-type="rssfeedmap">
                ${Object.entries(feeds).map(([feedId, cfg]) => renderRssFeedEntry(feedId, typeof cfg === 'string' ? { url: cfg } : (cfg || {})))}
            </div>
            <button type="button" class="btn-add-entry" data-click="addRssFeedEntry" data-click-args="${actionArgs(EL)}">+ Ajouter un flux</button>
            <select class="rssfeed-preset-select" data-change="applyRssPreset" data-change-args="${actionArgs(EL)}" title="Préréglages prêts à l'emploi">
                <option value="">+ Préréglage…</option>
                ${PRESET_GROUPS.map(([label, options]) => html`<optgroup label="${label}">${options.map(([id, name]) => html`<option value="${id}">${name}</option>`)}</optgroup>`)}
            </select>`;
    },
    collect: (list) => {
        const feeds = {};
        for (const entry of list.querySelectorAll('.rssfeed-entry')) {
            const field = (name) => entry.querySelector(`[data-rss-field="${name}"]`);
            const value = (name) => field(name)?.value?.trim() || '';
            const feedId = value('feed_id');
            const url = value('url');
            if (!feedId || !url) continue;
            const cfg = { url };
            const label = value('label');
            const channelId = channelPickerValue(field('channel_id'));
            const template = value('template');
            if (label) cfg.label = label;
            if (channelId) cfg.channel_id = channelId;
            if (template) cfg.template = template;
            if ((field('mode')?.value || 'text') === 'embed') {
                cfg.mode = 'embed';
                for (const [name, keyName] of [['embed_color', 'embed_color'], ['embed_title', 'embed_title'], ['embed_description', 'embed_description']]) {
                    if (value(name)) cfg[keyName] = value(name);
                }
                cfg.embed_show_image = field('embed_show_image')?.checked !== false;
                if (field('embed_show_link_button')?.checked) {
                    cfg.embed_show_link_button = true;
                    if (value('embed_link_button_label')) cfg.embed_link_button_label = value('embed_link_button_label');
                }
            }
            feeds[feedId] = cfg;
        }
        return feeds;
    },
};

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
    wrapper.innerHTML = String(renderRssFeedEntry('', {})).trim();
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
        wrapper.innerHTML = String(renderRssFeedEntry(preset.feed_id, { ...cfg, url: base + file })).trim();
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
