// Chart.js, loaded on demand with its SRI digest, and its theme.

// ── XP: activity stats + charts ─────────────────────────────────────
// Chart.js is loaded lazily, only once an XP module page is opened.
// Pinned build + SRI hash: bump both together.
export const CHARTJS_SRC = 'https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js';
export const CHARTJS_SRI = 'sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ';  // pragma: allowlist secret — public SRI digest
export let _chartJsPromise = null;

export function ensureChartJs() {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (!_chartJsPromise) {
        _chartJsPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = CHARTJS_SRC;
            script.integrity = CHARTJS_SRI;
            script.crossOrigin = 'anonymous';
            script.async = true;
            script.onload = () => (window.Chart ? resolve(window.Chart) : reject(new Error('Chart.js introuvable')));
            script.onerror = () => {
                script.remove();
                reject(new Error("Chart.js n'a pas pu être chargé"));
            };
            document.head.appendChild(script);
        }).catch(err => {
            _chartJsPromise = null;  // let the next load retry
            throw err;
        });
    }
    return _chartJsPromise;
}

export function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function withAlpha(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// Shared by every Chart.js view (XP, Spotify): axis text, gridlines and font
// follow the dashboard's theme tokens.
export function applyChartTheme(Chart) {
    Chart.defaults.color = cssVar('--text-secondary', '#b5bac1');
    Chart.defaults.borderColor = cssVar('--border', '#3f4147');
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
}
