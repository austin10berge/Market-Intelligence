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

    if (settings.sma.some((item) => item.enabled)) {
        studies.push("MAMultiple@tv-basicstudies");
    }

    if (settings.bollinger.enabled) {
        studies.push("BB@tv-basicstudies");
    }

    return studies;
}

export function buildWidgetStudyOverrides(settings) {
    const overrides = {
        // Bollinger Bands overrides
        "BB.length": settings.bollinger.length,
        "BB.mult": settings.bollinger.multiplier,
        // MAMultiple: set each of the 6 period slots; hide unused slots by
        // setting their length to something large and toggling display off.
        "MAMultiple.MA #1 Length": settings.sma[0].length,
        "MAMultiple.MA #2 Length": settings.sma[1].length,
        "MAMultiple.MA #3 Length": settings.sma[2].length,
        "MAMultiple.MA #4 Length": 400,
        "MAMultiple.MA #5 Length": 500,
        "MAMultiple.MA #6 Length": 600,
        // display bitmask: 15 = show on all panes, 0 = hidden
        "MAMultiple.plot_0.display": settings.sma[0].enabled ? 15 : 0,
        "MAMultiple.plot_1.display": settings.sma[1].enabled ? 15 : 0,
        "MAMultiple.plot_2.display": settings.sma[2].enabled ? 15 : 0,
        "MAMultiple.plot_3.display": 0,
        "MAMultiple.plot_4.display": 0,
        "MAMultiple.plot_5.display": 0,
    };

    return overrides;
}
