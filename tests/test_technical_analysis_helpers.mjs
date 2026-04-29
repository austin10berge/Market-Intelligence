import test from "node:test";
import assert from "node:assert/strict";

import {
    DEFAULT_INDICATOR_SETTINGS,
    buildStudyDefinitions,
    sanitizeIndicatorSettings,
} from "../src/web/technical-analysis-helpers.js";

test("sanitizeIndicatorSettings falls back to defaults for invalid payloads", () => {
    const result = sanitizeIndicatorSettings({
        sma: [{ enabled: true, length: -5 }],
        bollinger: { enabled: true, length: 0, multiplier: -1 },
        interval: "bad",
        theme: "alien",
    });

    assert.deepEqual(result.sma.map((item) => item.length), [20, 50, 200]);
    assert.equal(result.bollinger.length, 20);
    assert.equal(result.bollinger.multiplier, 2);
    assert.equal(result.interval, "D");
    assert.equal(result.theme, "dark");
});

test("buildStudyDefinitions includes enabled SMAs and Bollinger Bands with inputs", () => {
    const studies = buildStudyDefinitions({
        sma: [
            { enabled: true, length: 21 },
            { enabled: false, length: 50 },
            { enabled: true, length: 200 },
        ],
        bollinger: { enabled: true, length: 20, multiplier: 2 },
        interval: "D",
        theme: "dark",
    });

    assert.deepEqual(studies, [
        { name: "Moving Average", inputs: { length: 21 } },
        { name: "Moving Average", inputs: { length: 200 } },
        { name: "Bollinger Bands", inputs: { length: 20, mult: 2 } },
    ]);
});

test("sanitizeIndicatorSettings returns defaults when payload is missing", () => {
    assert.deepEqual(sanitizeIndicatorSettings(), DEFAULT_INDICATOR_SETTINGS);
});
