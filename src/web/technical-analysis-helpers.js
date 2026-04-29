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

    // tv.js only accepts string IDs in the studies array — inputs are
    // controlled via studies_overrides. We add one MA@tv-basicstudies
    // per enabled SMA slot so each gets its own override namespace
    // (MA@tv-basicstudies-0, MA@tv-basicstudies-1, etc.).
    settings.sma.forEach((item) => {
        if (item.enabled) {
            studies.push("MA@tv-basicstudies");
        }
    });

    if (settings.bollinger.enabled) {
        studies.push("BB@tv-basicstudies");
    }

    return studies;
}

export function buildWidgetStudyOverrides(settings) {
    const overrides = {};

    // tv.js indexes duplicate study IDs as "MA@tv-basicstudies-0",
    // "MA@tv-basicstudies-1", etc. We only add overrides for enabled SMAs
    // since disabled ones are not pushed into the studies array.
    let idx = 0;
    settings.sma.forEach((item) => {
        if (item.enabled) {
            const prefix = idx === 0 ? "MA@tv-basicstudies" : `MA@tv-basicstudies-${idx}`;
            overrides[`${prefix}.length`] = item.length;
            idx++;
        }
    });

    if (settings.bollinger.enabled) {
        overrides["BB@tv-basicstudies.length"] = settings.bollinger.length;
        overrides["BB@tv-basicstudies.mult"] = settings.bollinger.multiplier;
    }

    return overrides;
}
