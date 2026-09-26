# Normalization proof

Method in one paragraph: every review is reduced to a 0-100 score by equal-weighted rescaling of the three 1-5 criteria (functionality, quality, innovation), then fitted to the additive model `score = project quality + judge offset` by block coordinate descent minimizing `sum (s - mu - b)^2 + lambda * sum b^2` with `lambda = 2.0` (BUILD-SPEC 9). The normalized project score is `mu`; ranks below are competition ranks with ties shared at 2 dp. The portal results page runs this same code (`src/results/engine.py`), so these numbers match it exactly. Source: `fixtures.json` (121 included reviews over 40 projects after the duplicate exclusion).

## Fixture results

Duplicate handling: `prj_07` (titled "Dry Harbour") was superseded by `prj_41` and excluded with its 5 reviews; `prj_41` keeps its own 4 reviews. Judge `jdg_01` (Tomas Varga) reviewed only the superseded project, so they contribute 0 included reviews.

| Norm rank | Raw rank | Move | Project | Title | n | Raw | Normalized |
|---|---|---|---|---|---:|---:|---:|
| 1 | =1 | = | `prj_34` | Iron Switch | 3 | 83.33 | 82.57 |
| 2 | =1 | ▼1 | `prj_11` | Salt Ledger | 4 | 83.33 | 82.28 |
| 3 | 4 | ▲1 | `prj_25` | Dry Relay | 3 | 77.78 | 79.61 |
| 4 | 5 | ▲1 | `prj_37` | Salt Loom | 4 | 77.08 | 77.20 |
| 5 | =6 | ▲1 | `prj_16` | Salt Kiln | 3 | 75.00 | 76.71 |
| 6 | =6 | = | `prj_33` | Slow Trail | 3 | 75.00 | 75.44 |
| 7 | 3 | ▼4 | `prj_10` | Still Beacon | 2 | 79.17 | 75.12 |
| 8 | 8 | = | `prj_21` | Copper Kiln | 3 | 72.22 | 71.80 |
| 9 | =11 | ▲2 | `prj_38` | Deep Beacon | 3 | 69.44 | 71.28 |
| 10 | 10 | = | `prj_08` | North Drift | 5 | 70.00 | 70.51 |
| 11 | 9 | ▼2 | `prj_41` | Dry Harbour | 4 | 70.83 | 69.69 |
| 12 | =11 | ▼1 | `prj_04` | Green Switch | 3 | 69.44 | 68.66 |
| 13 | =13 | = | `prj_15` | Copper Orbit | 2 | 66.67 | 68.22 |
| 14 | =13 | ▼1 | `prj_36` | Salt Drift | 3 | 66.67 | 65.91 |
| 15 | =20 | ▲5 | `prj_18` | Open Kiln | 2 | 62.50 | 65.06 |
| =16 | =16 | = | `prj_09` | Hollow Signal | 3 | 63.89 | 64.53 |
| =16 | =16 | = | `prj_17` | Small Loom | 3 | 63.89 | 64.53 |
| 18 | =16 | ▼2 | `prj_31` | Salt Ferry | 3 | 63.89 | 64.33 |
| 19 | =13 | ▼6 | `prj_19` | Small Relay | 2 | 66.67 | 64.00 |
| 20 | =16 | ▼4 | `prj_02` | Small Meadow | 3 | 63.89 | 63.95 |
| 21 | =24 | ▲3 | `prj_12` | Open Beacon | 3 | 61.11 | 63.23 |
| 22 | =20 | ▼2 | `prj_24` | Glass Beacon | 2 | 62.50 | 62.44 |
| 23 | =20 | ▼3 | `prj_39` | Paper Anchor | 2 | 62.50 | 62.42 |
| 24 | =24 | = | `prj_32` | Loud Ledger | 3 | 61.11 | 61.55 |
| 25 | =24 | ▼1 | `prj_01` | Glass Signal | 3 | 61.11 | 61.47 |
| 26 | 29 | ▲3 | `prj_14` | Green Lantern | 5 | 60.00 | 61.27 |
| 27 | =24 | ▼3 | `prj_27` | Flat Thread | 3 | 61.11 | 60.35 |
| 28 | =30 | ▲2 | `prj_29` | Flat Relay | 2 | 58.33 | 60.20 |
| 29 | 23 | ▼6 | `prj_35` | Warm Beacon | 5 | 61.67 | 58.11 |
| 30 | =24 | ▼6 | `prj_28` | Flat Meadow | 3 | 61.11 | 57.64 |
| 31 | =30 | ▼1 | `prj_03` | Deep Compass | 3 | 58.33 | 56.06 |
| 32 | =30 | ▼2 | `prj_13` | Quiet Anchor | 3 | 58.33 | 55.45 |
| 33 | =35 | ▲2 | `prj_30` | Paper Harbour | 3 | 52.78 | 54.89 |
| 34 | =33 | ▼1 | `prj_20` | Paper Thread | 3 | 55.56 | 54.80 |
| 35 | =35 | = | `prj_06` | Dry Compass | 3 | 52.78 | 53.22 |
| 36 | =35 | ▼1 | `prj_22` | Dry Bridge | 3 | 52.78 | 53.01 |
| 37 | =33 | ▼4 | `prj_26` | Amber Hours | 3 | 55.56 | 52.42 |
| 38 | 38 | = | `prj_40` | Slow Loom | 2 | 50.00 | 50.20 |
| 39 | =39 | = | `prj_05` | North Compass | 3 | 47.22 | 46.80 |
| 40 | =39 | ▼1 | `prj_23` | Slow Quarry | 3 | 47.22 | 46.46 |

### Judge offsets (lambda = 2)

| Judge | Name | n | Mean given | Offset | Label | Spread |
|---|---|---:|---:|---:|---|---:|
| `jdg_10` | Hiro Tanaka | 3 | 55.56 | -9.75 | harsh | 10.39 |
| `jdg_27` | Leila Nasser | 2 | 50.00 | -7.27 | harsh | 8.33 |
| `jdg_25` | Thandi Dlamini | 5 | 61.67 | -6.21 | harsh | 10.00 |
| `jdg_14` | Emeka Adeyemi | 3 | 50.00 | -5.88 | harsh | 13.61 |
| `jdg_20` | Otto Brandt | 6 | 52.78 | -5.80 | harsh | 18.43 |
| `jdg_08` | Marek Nowak | 3 | 61.11 | -5.07 | harsh | 17.12 |
| `jdg_12` | Dilan Yilmaz | 1 | 50.00 | -4.65 | typical | 0.00 |
| `jdg_23` | Anya Sokolova | 1 | 58.33 | -4.06 | typical | 0.00 |
| `jdg_24` | Diego Herrera | 11 | 59.09 | -2.81 | typical | 14.85 |
| `jdg_04` | Noor Haddad | 4 | 64.58 | -2.31 | typical | 16.00 |
| `jdg_18` | Lars Berg | 3 | 61.11 | -2.24 | typical | 3.93 |
| `jdg_16` | Nadia Rahman | 6 | 65.28 | -2.22 | typical | 17.62 |
| `jdg_28` | Pavel Ivanov | 2 | 54.17 | -1.54 | typical | 4.17 |
| `jdg_29` | Ines Rocha | 9 | 62.96 | -1.06 | typical | 9.71 |
| `jdg_09` | Sofia Duarte | 5 | 61.67 | -0.91 | typical | 12.47 |
| `jdg_21` | Sana Aziz | 3 | 66.67 | +2.06 | typical | 20.41 |
| `jdg_11` | Amara Silva | 6 | 65.28 | +2.32 | typical | 17.62 |
| `jdg_22` | Felix Roth | 5 | 68.33 | +2.35 | typical | 17.00 |
| `jdg_19` | Mira Kaur | 3 | 66.67 | +2.36 | typical | 0.00 |
| `jdg_26` | Jonas Vogel | 9 | 64.81 | +2.41 | typical | 8.59 |
| `jdg_05` | Kofi Mensah | 2 | 62.50 | +2.86 | typical | 4.17 |
| `jdg_30` | Rafa Okonkwo | 4 | 77.08 | +2.92 | typical | 12.33 |
| `jdg_06` | Lena Kovac | 3 | 61.11 | +3.47 | typical | 10.39 |
| `jdg_17` | Bruno Costa | 2 | 62.50 | +3.62 | typical | 4.17 |
| `jdg_03` | Priya Nair | 2 | 70.83 | +5.52 | generous | 12.50 |
| `jdg_07` | Iva Petrova — constant scorer | 3 | 75.00 | +6.39 | generous | 0.00 |
| `jdg_13` | Rosa Moreau | 3 | 75.00 | +6.65 | generous | 13.61 |
| `jdg_15` | Yuki Sato | 6 | 76.39 | +9.14 | generous | 18.89 |
| `jdg_02` | Wei Lindqvist | 6 | 80.56 | +9.70 | generous | 18.43 |

`jdg_07` (Iva Petrova) is the constant scorer: 3 reviews, every criterion a 4. `jdg_01` gave a single review to superseded `prj_07`, so they have no included reviews and no offset (a single-review offset would be pure shrinkage anyway).

### Rank movements (raw → normalized): 29 of 40 projects move

- ▼6 `prj_19` (Small Relay): raw =13 → normalized 19
- ▼6 `prj_28` (Flat Meadow): raw =24 → normalized 30
- ▼6 `prj_35` (Warm Beacon): raw 23 → normalized 29
- ▲5 `prj_18` (Open Kiln): raw =20 → normalized 15
- ▼4 `prj_02` (Small Meadow): raw =16 → normalized 20
- ▼4 `prj_10` (Still Beacon): raw 3 → normalized 7
- ▼4 `prj_26` (Amber Hours): raw =33 → normalized 37
- ▲3 `prj_12` (Open Beacon): raw =24 → normalized 21
- ▲3 `prj_14` (Green Lantern): raw 29 → normalized 26
- ▼3 `prj_27` (Flat Thread): raw =24 → normalized 27
- ▼3 `prj_39` (Paper Anchor): raw =20 → normalized 23
- ▼2 `prj_13` (Quiet Anchor): raw =30 → normalized 32
- ▼2 `prj_24` (Glass Beacon): raw =20 → normalized 22
- ▲2 `prj_29` (Flat Relay): raw =30 → normalized 28
- ▲2 `prj_30` (Paper Harbour): raw =35 → normalized 33
- ▼2 `prj_31` (Salt Ferry): raw =16 → normalized 18
- ▲2 `prj_38` (Deep Beacon): raw =11 → normalized 9
- ▼2 `prj_41` (Dry Harbour): raw 9 → normalized 11
- ▼1 `prj_01` (Glass Signal): raw =24 → normalized 25
- ▼1 `prj_03` (Deep Compass): raw =30 → normalized 31
- ▼1 `prj_04` (Green Switch): raw =11 → normalized 12
- ▼1 `prj_11` (Salt Ledger): raw =1 → normalized 2
- ▲1 `prj_16` (Salt Kiln): raw =6 → normalized 5
- ▼1 `prj_20` (Paper Thread): raw =33 → normalized 34
- ▼1 `prj_22` (Dry Bridge): raw =35 → normalized 36
- ▼1 `prj_23` (Slow Quarry): raw =39 → normalized 40
- ▲1 `prj_25` (Dry Relay): raw 4 → normalized 3
- ▼1 `prj_36` (Salt Drift): raw =13 → normalized 14
- ▲1 `prj_37` (Salt Loom): raw 5 → normalized 4

### Spread before/after: 8.09 → 4.69

Sigma of per-judge mean scores drops from 8.09 raw to 4.69 after offset removal: most of the disagreement between judges' average marks is level, not ordering.

### The judge who marks everything the same

`jdg_07` (Iva Petrova) gave 3 reviews with identical criterion values everywhere (all 4s, score 75.00). The model absorbs that level into their offset (+6.39, labelled generous) and their reviews add no ordering information: they only pull their three projects toward a common value. They are kept by default and shown with this explanation; an organizer may exclude them with a recorded reason before publication.
Excluding `jdg_07` changes the normalized rank of 18 project(s): `prj_01` (Glass Signal: 25 → 22), `prj_02` (Small Meadow: 20 → 17), `prj_09` (Hollow Signal: =16 → =24), `prj_11` (Salt Ledger: 2 → 1), `prj_12` (Open Beacon: 21 → 18), `prj_14` (Green Lantern: 26 → 23), `prj_17` (Small Loom: =16 → =24), `prj_18` (Open Kiln: 15 → 14), `prj_19` (Small Relay: 19 → 30), `prj_24` (Glass Beacon: 22 → 19), `prj_28` (Flat Meadow: 30 → 29), `prj_29` (Flat Relay: 28 → 26), `prj_31` (Salt Ferry: 18 → 16), `prj_32` (Loud Ledger: 24 → 21), `prj_34` (Iron Switch: 1 → 2), `prj_35` (Warm Beacon: 29 → 28), `prj_36` (Salt Drift: 14 → 15), `prj_39` (Paper Anchor: 23 → 20).

### Outliers (|residual| > 2.5 x SD, projects with >= 3 reviews)

- `jdg_04` (Noor Haddad) scored `prj_37` (Salt Loom) 33.2 points below consensus (score 41.67).
- `jdg_15` (Yuki Sato) scored `prj_27` (Flat Thread) 27.8 points below consensus (score 41.67).

Outliers are detection-only signals for organizers (possible collusion or undeclared conflict) and are never auto-excluded.

### Lambda sensitivity (vs lambda = 2)

| λ | Top-5 (normalized) | Spearman ρ vs λ=2 |
|---:|---|---:|
| 0 | `prj_11`, `prj_25`, `prj_16`, `prj_21`, `prj_38` | 0.8313 |
| 1 | `prj_11`, `prj_34`, `prj_25`, `prj_16`, `prj_37` | 0.9944 |
| 2 | `prj_34`, `prj_11`, `prj_25`, `prj_37`, `prj_16` | 1.0000 |
| 5 | `prj_34`, `prj_11`, `prj_25`, `prj_37`, `prj_10` | 0.9936 |

Judge–project graph components: 1 (cross-component comparisons would be flagged weakly supported). Under-reviewed (< 3): `prj_10`, `prj_15`, `prj_18`, `prj_19`, `prj_24`, `prj_29`, `prj_39`, `prj_40`. Single-review judges (included reviews): `jdg_12`, `jdg_23`. Derived Bradley–Terry cross-check top-5: `prj_34`, `prj_33`, `prj_37`, `prj_02`, `prj_11`.

## Simulations

Design: the fixture's exact judge–project review pattern (121 pairs). Per replication: true project quality ~ N(0,1), judge offsets ~ N(0, σ_b) with σ_b in {0.3, 0.6, 1.0} score points on the 1–5 scale, noise ~ N(0, 0.5) per criterion, rounded and clipped to integer 1–5; one constant judge (`jdg_07`, all 4s) like the fixture. 500 replications per σ_b. Methods: raw mean, per-judge z-score (zero-variance judges skipped), additive λ=0, additive λ=2, derived Bradley–Terry. Reported: mean Spearman ρ with truth, top-5 recall, share of replications where the true best project ranks first.

### σ_b = 0.3 (seed 1101)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.943 | 0.803 | 0.636 |
| zscore | 0.876 | 0.666 | 0.312 |
| add0 | 0.888 | 0.702 | 0.434 |
| add2 | 0.952 | 0.818 | 0.634 |
| bt | 0.901 | 0.678 | 0.350 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

### σ_b = 0.6 (seed 2202)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.904 | 0.743 | 0.540 |
| zscore | 0.876 | 0.632 | 0.296 |
| add0 | 0.875 | 0.676 | 0.440 |
| add2 | 0.930 | 0.780 | 0.568 |
| bt | 0.898 | 0.658 | 0.348 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

### σ_b = 1.0 (seed 3303)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.820 | 0.624 | 0.426 |
| zscore | 0.869 | 0.613 | 0.280 |
| add0 | 0.852 | 0.634 | 0.380 |
| add2 | 0.879 | 0.690 | 0.484 |
| bt | 0.892 | 0.654 | 0.304 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

## Properties

Shift invariance: adding +1 to every criterion value of one judge (`jdg_07`, chosen because 4+1 needs no clipping) moves the raw ranking but leaves the additive (λ=0) ranking exactly unchanged — the shift is absorbed by that judge's offset:
- raw ranking positions changed: 17 of 40;
- additive (λ=0) rankings identical: True.

Constant-judge case: a judge with zero variance breaks per-judge z-scores (division by zero) and their scores depend on which projects they happened to receive; the additive model instead absorbs their level into the offset (+6.39) and their reviews contribute no ordering information. That is why the portal uses offsets, not z-scores (see JUDGING.md).

## Limitations

- Offset-only model: linear level habits are removed, but scale habits (harsh on weak projects, generous on strong ones) and nonlinear mappings are not modelled.
- Few reviews per judge: single-review offsets are almost pure shrinkage toward zero; their projects are ranked mostly by raw means.
- Clipping at the scale ends (1 and 5) destroys level information a shift would otherwise preserve.
- Correlated or strategic bias (vote-trading, team-targeted collusion) is not addressed by normalization; see the outlier list and THREAT-MODEL.md.

## Reproducibility

Regenerate with `.venv\Scripts\python.exe scripts/normalization_proof.py` (standard library only; reads `fixtures.json`, imports `src/results/engine.py`). Simulation seeds: σ_b=0.3 → 1101, σ_b=0.6 → 2202, σ_b=1.0 → 3303.
