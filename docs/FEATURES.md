# Feature catalogue

Generated from `src/features/catalogue.py`. Do not edit by hand.

CLAUDE.md §4: every feature states its source, its lag, and its
economic rationale. A feature without a reason does not belong here.

| Feature | Source field | Lag (ISPs) | Rationale |
|---|---|---|---|
| `lag_price_short_1` | `imbalance_price_settled` | 1 | Most recent settled short price. Imbalance prices are strongly autocorrelated at short lags, so this is the single most informative cheap feature and the persistence baseline's input. |
| `lag_price_short_96` | `imbalance_price_settled` | 96 | Same ISP yesterday. Captures the daily shape of demand and renewable output that repeats across days, and is the seasonal naive baseline's input. |
| `lag_price_short_672` | `imbalance_price_settled` | 672 | Same ISP last week. Captures day-of-week structure -- weekend load and industrial demand differ systematically from weekdays. |
| `lag_spread_1` | `imbalance_price_settled` | 1 | Previous ISP's long-short gap. Non-zero means the previous period was dual-priced, and dual pricing clusters: state 2 arises from intra-period volatility, which persists across period boundaries. |
| `hour_sin` | `calendar` | 0 | Cyclic encoding of hour-of-day. Raw integers tell a linear model hour 23 and hour 0 are 23 apart when they are adjacent. |
| `hour_cos` | `calendar` | 0 | Cyclic encoding of hour-of-day; the paired cosine term. |
| `dow_sin` | `calendar` | 0 | Cyclic encoding of day-of-week, so Sunday and Monday are adjacent rather than six days apart. |
| `dow_cos` | `calendar` | 0 | Cyclic encoding of day-of-week; the paired cosine term. |
| `hour` | `calendar` | 0 | Raw hour, used as a grouping key by the climatological and conditional-frequency baselines rather than as a model input. |
| `dayofweek` | `calendar` | 0 | Raw day-of-week, used as a grouping key by the climatological and conditional-frequency baselines. |
| `day_ahead_price` | `day_ahead_price` | 0 | The day-ahead auction price for this ISP, published by ~13:00 on D-1 -- fully available before delivery day D begins, so no extra shift is needed. Feeds the day-ahead baseline and T3 (spread vs day-ahead). Always present in the output (NaN when no day-ahead series is supplied) so the column set never depends on which optional inputs the caller happened to pass. |
