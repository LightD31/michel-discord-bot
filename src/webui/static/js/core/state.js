// The app's shared state.

// ── State ─────────────────────────────────────────
export const state = {
    user: null,
    currentPage: 'home',
    currentServerId: null,
    currentModule: null,
    config: null,
    servers: null,
    botInfo: null,
    moduleSchemas: {},
    globalSchemas: {},
    extensions: [],
    sidebarOpen: false,
    // Log viewer state
    logs: [],
    logLevel: '',
    logSearch: '',
    logAutoScroll: true,
    logStreaming: false,
    logVerbose: false,
    // XP module stats: selected window (days)
    xpStatsDays: 30,
    // Set on /public/{token}/{kind}: { token, kind, meta, error }.
    // The page then renders the read-only public dashboard, no login.
    publicView: null,
    // Short-link manager (Shlink) state, for the server whose module page is open
    shlink: {
        serverId: null,
        status: null,
        items: [],
        pagination: {},
        page: 1,
        search: '',
        editing: null,
        loading: false,
    },
};
