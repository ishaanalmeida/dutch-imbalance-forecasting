# Feature catalogue

Generated from `src/features/catalogue.py`. Do not edit by hand.

CLAUDE.md §4: every feature states its source, its lag, and its
economic rationale. A feature without a reason does not belong here.

| Feature | Source field | Lag (ISPs) | Rationale |
|---|---|---|---|
| `lag_price_short_96` | `imbalance_price_settled` | 96 | Same ISP yesterday. Captures the daily shape of demand and renewable output that repeats across days. Settlement publishes once daily at D+1 10:00 (config/market_rules.yaml), so this is only available for decisions taken after ~10:00 local -- masked to NaN for the rest (ADR-023). Never contemporaneous. |
| `lag_price_short_192` | `imbalance_price_settled` | 192 | Same ISP two days back. The D+1 10:00 settlement rule means a two-day-old settled price is always available, whatever the decision time's hour-of-day -- the freshest settled lag that needs no availability mask, anchoring the feature set even for early-morning decisions when lag_price_short_96 is masked (ADR-023). |
| `lag_price_short_freshest` | `imbalance_price_settled` | 96 | The most recent settled price actually visible at decision time: lag_price_short_96 where the 10:00 settlement run has already posted it, else lag_price_short_192, which is always available. This is the honest 'last observed value' and what a persistence baseline genuinely has -- a plain one-ISP lag is never available in this market and was removed (ADR-023). |
| `lag_price_short_672` | `imbalance_price_settled` | 672 | Same ISP last week. Captures day-of-week structure -- weekend load and industrial demand differ systematically from weekdays. Always available at decision time regardless of hour-of-day. |
| `lag_spread_96` | `imbalance_price_settled` | 96 | Yesterday's same-ISP long-short gap, masked by the same D+1 10:00 settlement rule as the price lags above. Non-zero means that ISP was dual-priced, and dual pricing clusters, so it remains informative a full day lagged; the previous-ISP version was never available and was removed (ADR-023). |
| `hour_sin` | `calendar` | 0 | Cyclic encoding of hour-of-day. Raw integers tell a linear model hour 23 and hour 0 are 23 apart when they are adjacent. |
| `hour_cos` | `calendar` | 0 | Cyclic encoding of hour-of-day; the paired cosine term. |
| `dow_sin` | `calendar` | 0 | Cyclic encoding of day-of-week, so Sunday and Monday are adjacent rather than six days apart. |
| `dow_cos` | `calendar` | 0 | Cyclic encoding of day-of-week; the paired cosine term. |
| `hour` | `calendar` | 0 | Raw hour, used as a grouping key by the climatological and conditional-frequency baselines rather than as a model input. |
| `dayofweek` | `calendar` | 0 | Raw day-of-week, used as a grouping key by the climatological and conditional-frequency baselines. |
| `day_ahead_price` | `day_ahead_price` | 0 | The day-ahead auction price for this ISP, published by ~13:00 on D-1 -- fully available before delivery day D begins, so no extra shift is needed. Feeds the day-ahead baseline and T3 (spread vs day-ahead). Always present in the output (NaN when no day-ahead series is supplied) so the column set never depends on which optional inputs the caller happened to pass. |
