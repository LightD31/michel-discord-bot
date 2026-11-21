// What the app loads at start: the user, the bot, servers, schemas, config.
import { registerActions } from './actions.js';
import { api } from './api.js';
import { state } from './state.js';

// ── Data loading ──────────────────────────────────
export async function loadUserInfo() {
    const data = await api('/api/me');
    if (data && data.authenticated) {
        state.user = data;
    } else {
        state.user = null;
    }
}

export function isAdmin() {
    return state.user && state.user.is_admin;
}

export function isDeveloper() {
    return state.user && state.user.is_developer;
}

export async function loadBotInfo() {
    try {
        state.botInfo = await api('/api/bot-info');
    } catch (e) {
        state.botInfo = { status: 'error' };
    }
}

export async function loadExtensions() {
    try {
        const data = await api('/api/extensions');
        state.extensions = data.extensions || [];
    } catch (e) {
        state.extensions = [];
    }
}

export async function loadServers() {
    try {
        state.servers = await api('/api/servers');
    } catch (e) {
        state.servers = {};
    }
}

export async function loadSchemas() {
    try {
        const [modSchemas, globalSchemas] = await Promise.all([
            api('/api/schemas/modules'),
            api('/api/schemas/global'),
        ]);
        state.moduleSchemas = modSchemas || {};
        state.globalSchemas = globalSchemas || {};
    } catch (e) {
        state.moduleSchemas = {};
        state.globalSchemas = {};
    }
    try {
        state.templateTests = (await api('/api/template-tests')) || {};
    } catch (e) {
        state.templateTests = {};
    }
}

export async function loadConfig() {
    try {
        state.config = await api('/api/config');
    } catch (e) {
        state.config = {};
    }
}

export async function logout() {
    try {
        await fetch('/auth/logout', { method: 'POST' });
    } finally {
        window.location = '/';
    }
}

registerActions({
    logout,
});
