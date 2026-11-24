// Entry point: loads every module (each registers its actions), then starts the app.
// Every module, for the actions it registers and the listeners it sets.
import './core/actions.js';
import './core/api.js';
import './core/data.js';
import './core/dirty.js';
import './core/html.js';
import './core/render.js';
import './core/router.js';
import './core/state.js';
import './core/util.js';
import './features/gazette.js';
import './features/moderation.js';
import './features/rolemenus.js';
import './features/secretsanta.js';
import './features/spotify-auth.js';
import './features/twitch-auth.js';
import './features/spotify.js';
import './features/xp.js';
import './forms/module-copy.js';
import './forms/checks.js';
import './forms/pickers/channels.js';
import './forms/pickers/lists.js';
import './forms/pickers/members.js';
import './forms/pickers/roles.js';
import './forms/save-bar.js';
import './forms/fields.js';
import './forms/save.js';
import './forms/schema-form.js';
import './forms/template-test.js';
import './forms/widgets/accounts.js';
import './forms/widgets/commands.js';
import './forms/widgets/embeds.js';
import './forms/widgets/maps.js';
import './forms/widgets/messages.js';
import './forms/widgets/models.js';
import './forms/widgets/rss.js';
import './forms/widgets/spotify-users.js';
import './forms/widgets/streamers.js';
import './forms/widgets/teams.js';
import './forms/widgets/youtube.js';
import './pages/audit.js';
import './pages/bot-messages.js';
import './pages/extensions.js';
import './pages/global.js';
import './pages/health.js';
import './pages/home.js';
import './pages/links.js';
import './pages/logs.js';
import './pages/me.js';
import './pages/module.js';
import './pages/onboarding.js';
import './features/achievements.js';
import './pages/agenda.js';
import './pages/incidents.js';
import './pages/overview.js';
import './pages/public.js';
import './pages/server.js';
import './pages/setup.js';
import './ui/charts.js';
import './ui/confirm.js';
import './ui/discord.js';
import './ui/modal.js';
import './ui/page-index.js';
import './ui/sidebar.js';
import './ui/toast.js';
import { isAdmin, isDeveloper, loadBotInfo, loadConfig, loadExtensions, loadSchemas, loadServers, loadUserInfo } from './core/data.js';
import { render } from './core/render.js';
import { navigate, parseHash } from './core/router.js';
import { state } from './core/state.js';
import { loadLogs } from './pages/logs.js';
import { initPublic, parsePublicPath } from './pages/public.js';
import { toast } from './ui/toast.js';

// ── Initialize ────────────────────────────────────
async function init() {
    const publicRoute = parsePublicPath();
    if (publicRoute) {
        await initPublic(publicRoute);
        return;
    }
    await loadUserInfo();
    if (state.user) {
        const promises = [loadBotInfo()];
        if (isAdmin()) {
            promises.push(loadServers(), loadSchemas());
        }
        if (isDeveloper()) {
            promises.push(loadConfig(), loadLogs(), loadExtensions());
        }
        await Promise.all(promises);
    }

    // Handle the redirect back from an OAuth callback (Spotify, Twitch; a hash-routed link).
    const hashParams = new URLSearchParams((location.hash.split('?')[1]) || '');
    // Only the outcome comes back, never a text (a link could carry any), and
    // only who could start the flow is told about it: a server's admins for
    // its Spotify account (P36), a developer for the bot's Twitch account.
    for (const [param, service, may] of [['spotify_auth', 'Spotify', isAdmin], ['twitch_auth', 'Twitch', isDeveloper]]) {
        if (!hashParams.get(param) || !may()) continue;
        const ok = hashParams.get(param) === 'ok';
        toast(ok ? `${service} connecté.` : `Échec de la connexion ${service} : voir les logs du bot.`,
            ok ? 'success' : 'error');
    }
    if (state.user) {
        const route = parseHash();
        navigate(route.page, route.serverId, route.moduleName, { fromHistory: true });
    } else {
        render();
    }

    // Periodically refresh bot info
    setInterval(async () => {
        if (state.user) {
            await loadBotInfo();
            if (state.currentPage === 'home') render();
        }
    }, 30000);
}

init();
