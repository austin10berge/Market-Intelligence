export const INDICATOR_STORAGE_KEY = "market-intelligence:csp-technical-analysis";

export const DEFAULT_INDICATOR_SETTINGS = {
    sma: [
        { enabled: true, length: 20 },
        { enabled: true, length: 50 },
        { enabled: true, length: 200 },
    ],
    bollinger: {
        enabled: true,
        length: 20,
        multiplier: 2,
    },
    interval: "D",
    theme: "dark",
};

const ALLOWED_INTERVALS = new Set(["D", "240", "W"]);
const ALLOWED_THEMES = new Set(["dark", "light"]);

function clampInt(value, fallback, min, max) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) return fallback;
    if (parsed < min || parsed > max) return fallback;
    return parsed;
}

function clampFloat(value, fallback, min, max) {
    const parsed = Number.parseFloat(value);
    if (!Number.isFinite(parsed)) return fallback;
    if (parsed < min || parsed > max) return fallback;
    return parsed;
}

export function sanitizeIndicatorSettings(raw = {}) {
    const fallback = DEFAULT_INDICATOR_SETTINGS;
    const rawSma = Array.isArray(raw.sma) ? raw.sma : fallback.sma;

    return {
        sma: fallback.sma.map((item, index) => ({
            enabled: typeof rawSma[index]?.enabled === "boolean" ? rawSma[index].enabled : item.enabled,
            length: clampInt(rawSma[index]?.length, item.length, 2, 400),
        })),
        bollinger: {
            enabled: typeof raw.bollinger?.enabled === "boolean"
                ? raw.bollinger.enabled
                : fallback.bollinger.enabled,
            length: clampInt(raw.bollinger?.length, fallback.bollinger.length, 2, 400),
            multiplier: clampFloat(
                raw.bollinger?.multiplier,
                fallback.bollinger.multiplier,
                0.1,
                5,
            ),
        },
        interval: ALLOWED_INTERVALS.has(raw.interval) ? raw.interval : fallback.interval,
        theme: ALLOWED_THEMES.has(raw.theme) ? raw.theme : fallback.theme,
    };
}

export function buildStudyDefinitions(settings) {
    const studies = [];

    settings.sma.forEach((item) => {
        if (item.enabled) {
            studies.push({
                name: "Moving Average",
                inputs: { length: item.length },
            });
        }
    });

    if (settings.bollinger.enabled) {
        studies.push({
            name: "Bollinger Bands",
            inputs: {
                length: settings.bollinger.length,
                mult: settings.bollinger.multiplier,
            },
        });
    }

    return studies;
}

export function buildWidgetStudies(settings) {
    const studies = [];

    // embed-widget-advanced-chart.js accepts study objects with `id` and `inputs`.
    // The correct internal ID for a Simple Moving Average is "MASimple@tv-basicstudies".
    // SMA color palette — one color per slot in order.
    // To change a color: edit the hex value. To change opacity: edit the alpha (0.0–1.0).
    const SMA_COLORS = [
        "rgba(255, 179, 0, 1.0)",   // SMA 1 — amber
        "rgba(33, 150, 243, 1.0)",  // SMA 2 — blue
        "rgba(76, 175, 80, 1.0)",   // SMA 3 — green
    ];

    let colorIdx = 0;
    settings.sma.forEach((item) => {
        if (item.enabled) {
            const color = SMA_COLORS[colorIdx] ?? "rgba(255, 255, 255, 1.0)";
            studies.push({
                id: "MASimple@tv-basicstudies",
                inputs: { length: item.length },
                styles: {
                    "Plot.color": color,
                    "Plot.linewidth": 2,
                },
            });
            colorIdx++;
        }
    });

    if (settings.bollinger.enabled) {
        studies.push({
            id: "BB@tv-basicstudies",
            inputs: {
                length: settings.bollinger.length,
                mult: settings.bollinger.multiplier,
            },
        });
    }

    return studies;
}

export function buildWidgetStudyOverrides(_settings) {
    // Not used with embed-widget-advanced-chart.js — inputs are passed
    // directly in the studies array objects above.
    return {};
}

export const SCANNER_PARAMS_STORAGE_KEY = "market-intelligence:csp-scanner-params";

export const DEFAULT_SCANNER_PARAMS = {
    min_cap:   10,
    max_price: 150,
    min_beta:  0.8,
    max_beta:  2.4,
    min_vol:   30,
    rsi_max:   50,
    adx_min:   15,
    adx_max:   50,
    dte_min:   3,
    dte_max:   46,
    conditions: [],
};

export function loadScannerParams() {
    try {
        const raw = window.localStorage.getItem(SCANNER_PARAMS_STORAGE_KEY);
        if (!raw) return { ...DEFAULT_SCANNER_PARAMS };
        const parsed = JSON.parse(raw);
        return {
            min_cap:   typeof parsed.min_cap   === 'number' ? parsed.min_cap   : DEFAULT_SCANNER_PARAMS.min_cap,
            max_price: typeof parsed.max_price === 'number' ? parsed.max_price : DEFAULT_SCANNER_PARAMS.max_price,
            min_beta:  typeof parsed.min_beta  === 'number' ? parsed.min_beta  : DEFAULT_SCANNER_PARAMS.min_beta,
            max_beta:  typeof parsed.max_beta  === 'number' ? parsed.max_beta  : DEFAULT_SCANNER_PARAMS.max_beta,
            min_vol:   typeof parsed.min_vol   === 'number' ? parsed.min_vol   : DEFAULT_SCANNER_PARAMS.min_vol,
            rsi_max:   typeof parsed.rsi_max   === 'number' ? parsed.rsi_max   : DEFAULT_SCANNER_PARAMS.rsi_max,
            adx_min:   typeof parsed.adx_min   === 'number' ? parsed.adx_min   : DEFAULT_SCANNER_PARAMS.adx_min,
            adx_max:   typeof parsed.adx_max   === 'number' ? parsed.adx_max   : DEFAULT_SCANNER_PARAMS.adx_max,
            dte_min:   typeof parsed.dte_min   === 'number' ? parsed.dte_min   : DEFAULT_SCANNER_PARAMS.dte_min,
            dte_max:   typeof parsed.dte_max   === 'number' ? parsed.dte_max   : DEFAULT_SCANNER_PARAMS.dte_max,
            conditions: Array.isArray(parsed.conditions) ? parsed.conditions : [],
        };
    } catch {
        return { ...DEFAULT_SCANNER_PARAMS };
    }
}

export function buildScanQueryString(params) {
    const qs = new URLSearchParams({
        min_cap:   params.min_cap,
        max_price: params.max_price,
        min_beta:  params.min_beta,
        max_beta:  params.max_beta,
        min_vol:   params.min_vol,
        max_rsi:   params.rsi_max,
        min_adx:   params.adx_min,
        max_adx:   params.adx_max,
        min_dte:   params.dte_min,
        max_dte:   params.dte_max,
    });
    if (params.conditions.length) {
        qs.set('conditions', params.conditions.join(','));
    }
    return qs.toString();
}
