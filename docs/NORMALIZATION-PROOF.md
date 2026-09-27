# Normalization proof

Method in one paragraph: every review is reduced to a 0-100 score by equal-weighted rescaling of the three 1-5 criteria (functionality, quality, innovation), then fitted to the additive model `score = project quality + judge offset` by block coordinate descent minimizing `sum (s - mu - b)^2 + lambda * sum b^2` (BUILD-SPEC 9). The normalized project score is `mu`; ranks below are competition ranks with ties shared at 2 dp. Lambda is not hand-picked: the locked policy selects it by seeded 5-fold cross-validation over the grid (0.5, 1, 2, 5, 10, 20, 50, 100) at calculation time (ties go to the larger, more conservative value; BUILD-SPEC 19), and the chosen value is recorded with the result. On this fixture the procedure selects `lambda = 100` (see the CV table below). The portal results page runs this same code (`src/results/engine.py`), so these numbers match it exactly. Source: `fixtures.json` (121 included reviews over 40 projects after the duplicate exclusion).

## Fixture results

Duplicate handling: `prj_07` (titled "Dry Harbour") was superseded by `prj_41` and excluded with its 5 reviews; `prj_41` keeps its own 4 reviews. Judge `jdg_01` (Tomas Varga) reviewed only the superseded project, so they contribute 0 included reviews.

| Norm rank | Raw rank | Move | Project | Title | n | Raw | Normalized |
|---|---|---|---|---|---:|---:|---:|
| 1 | =1 | = | `prj_34` | Iron Switch | 3 | 83.33 | 83.35 |
| 2 | =1 | ▼1 | `prj_11` | Salt Ledger | 4 | 83.33 | 83.26 |
| 3 | 3 | = | `prj_10` | Still Beacon | 2 | 79.17 | 78.95 |
| 4 | 4 | = | `prj_25` | Dry Relay | 3 | 77.78 | 77.92 |
| 5 | 5 | = | `prj_37` | Salt Loom | 4 | 77.08 | 77.09 |
| 6 | =6 | = | `prj_33` | Slow Trail | 3 | 75.00 | 75.01 |
| 7 | =6 | ▼1 | `prj_16` | Salt Kiln | 3 | 75.00 | 74.98 |
| 8 | 8 | = | `prj_21` | Copper Kiln | 3 | 72.22 | 72.18 |
| 9 | 9 | = | `prj_41` | Dry Harbour | 4 | 70.83 | 70.77 |
| 10 | 10 | = | `prj_08` | North Drift | 5 | 70.00 | 69.94 |
| 11 | =11 | = | `prj_38` | Deep Beacon | 3 | 69.44 | 69.58 |
| 12 | =11 | ▼1 | `prj_04` | Green Switch | 3 | 69.44 | 69.38 |
| 13 | =13 | = | `prj_15` | Copper Orbit | 2 | 66.67 | 66.71 |
| 14 | =13 | ▼1 | `prj_36` | Salt Drift | 3 | 66.67 | 66.68 |
| 15 | =13 | ▼2 | `prj_19` | Small Relay | 2 | 66.67 | 66.61 |
| =16 | =16 | = | `prj_09` | Hollow Signal | 3 | 63.89 | 63.94 |
| =16 | =16 | = | `prj_17` | Small Loom | 3 | 63.89 | 63.94 |
| 18 | =16 | ▼2 | `prj_31` | Salt Ferry | 3 | 63.89 | 63.90 |
| 19 | =16 | ▼3 | `prj_02` | Small Meadow | 3 | 63.89 | 63.83 |
| 20 | =20 | = | `prj_18` | Open Kiln | 2 | 62.50 | 62.67 |
| 21 | =20 | ▼1 | `prj_24` | Glass Beacon | 2 | 62.50 | 62.52 |
| 22 | =20 | ▼2 | `prj_39` | Paper Anchor | 2 | 62.50 | 62.44 |
| 23 | 23 | = | `prj_35` | Warm Beacon | 5 | 61.67 | 61.57 |
| 24 | =24 | = | `prj_12` | Open Beacon | 3 | 61.11 | 61.20 |
| 25 | =24 | ▼1 | `prj_01` | Glass Signal | 3 | 61.11 | 61.14 |
| 26 | =24 | ▼2 | `prj_27` | Flat Thread | 3 | 61.11 | 61.13 |
| 27 | =24 | ▼3 | `prj_32` | Loud Ledger | 3 | 61.11 | 61.12 |
| 28 | =24 | ▼4 | `prj_28` | Flat Meadow | 3 | 61.11 | 60.88 |
| 29 | 29 | = | `prj_14` | Green Lantern | 5 | 60.00 | 60.05 |
| 30 | =30 | = | `prj_29` | Flat Relay | 2 | 58.33 | 58.46 |
| 31 | =30 | ▼1 | `prj_13` | Quiet Anchor | 3 | 58.33 | 58.26 |
| 32 | =30 | ▼2 | `prj_03` | Deep Compass | 3 | 58.33 | 58.21 |
| 33 | =33 | = | `prj_20` | Paper Thread | 3 | 55.56 | 55.57 |
| 34 | =33 | ▼1 | `prj_26` | Amber Hours | 3 | 55.56 | 55.47 |
| 35 | =35 | = | `prj_30` | Paper Harbour | 3 | 52.78 | 52.86 |
| 36 | =35 | ▼1 | `prj_22` | Dry Bridge | 3 | 52.78 | 52.84 |
| 37 | =35 | ▼2 | `prj_06` | Dry Compass | 3 | 52.78 | 52.79 |
| 38 | 38 | = | `prj_40` | Slow Loom | 2 | 50.00 | 50.00 |
| 39 | =39 | = | `prj_23` | Slow Quarry | 3 | 47.22 | 47.24 |
| 40 | =39 | ▼1 | `prj_05` | North Compass | 3 | 47.22 | 47.18 |

### Judge offsets (adaptive lambda = 100)

| Judge | Name | n | Mean given | Offset | Label | Spread |
|---|---|---:|---:|---:|---|---:|
| `jdg_20` | Otto Brandt | 6 | 52.78 | -0.47 | typical | 18.43 |
| `jdg_10` | Hiro Tanaka | 3 | 55.56 | -0.44 | typical | 10.39 |
| `jdg_25` | Thandi Dlamini | 5 | 61.67 | -0.40 | typical | 10.00 |
| `jdg_27` | Leila Nasser | 2 | 50.00 | -0.27 | typical | 8.33 |
| `jdg_14` | Emeka Adeyemi | 3 | 50.00 | -0.23 | typical | 13.61 |
| `jdg_24` | Diego Herrera | 11 | 59.09 | -0.23 | typical | 14.85 |
| `jdg_08` | Marek Nowak | 3 | 61.11 | -0.22 | typical | 17.12 |
| `jdg_29` | Ines Rocha | 9 | 62.96 | -0.19 | typical | 9.71 |
| `jdg_16` | Nadia Rahman | 6 | 65.28 | -0.18 | typical | 17.62 |
| `jdg_12` | Dilan Yilmaz | 1 | 50.00 | -0.14 | typical | 0.00 |
| `jdg_18` | Lars Berg | 3 | 61.11 | -0.12 | typical | 3.93 |
| `jdg_23` | Anya Sokolova | 1 | 58.33 | -0.11 | typical | 0.00 |
| `jdg_04` | Noor Haddad | 4 | 64.58 | -0.10 | typical | 16.00 |
| `jdg_28` | Pavel Ivanov | 2 | 54.17 | -0.06 | typical | 4.17 |
| `jdg_09` | Sofia Duarte | 5 | 61.67 | -0.03 | typical | 12.47 |
| `jdg_05` | Kofi Mensah | 2 | 62.50 | +0.05 | typical | 4.17 |
| `jdg_21` | Sana Aziz | 3 | 66.67 | +0.07 | typical | 20.41 |
| `jdg_06` | Lena Kovac | 3 | 61.11 | +0.08 | typical | 10.39 |
| `jdg_17` | Bruno Costa | 2 | 62.50 | +0.08 | typical | 4.17 |
| `jdg_19` | Mira Kaur | 3 | 66.67 | +0.08 | typical | 0.00 |
| `jdg_11` | Amara Silva | 6 | 65.28 | +0.09 | typical | 17.62 |
| `jdg_30` | Rafa Okonkwo | 4 | 77.08 | +0.16 | typical | 12.33 |
| `jdg_03` | Priya Nair | 2 | 70.83 | +0.19 | typical | 12.50 |
| `jdg_22` | Felix Roth | 5 | 68.33 | +0.20 | typical | 17.00 |
| `jdg_26` | Jonas Vogel | 9 | 64.81 | +0.23 | typical | 8.59 |
| `jdg_07` | Iva Petrova — constant scorer | 3 | 75.00 | +0.30 | typical | 0.00 |
| `jdg_13` | Rosa Moreau | 3 | 75.00 | +0.34 | typical | 13.61 |
| `jdg_15` | Yuki Sato | 6 | 76.39 | +0.62 | typical | 18.89 |
| `jdg_02` | Wei Lindqvist | 6 | 80.56 | +0.71 | typical | 18.43 |

`jdg_07` (Iva Petrova) is the constant scorer: 3 reviews, every criterion a 4. `jdg_01` gave a single review to superseded `prj_07`, so they have no included reviews and no offset (a single-review offset would be pure shrinkage anyway).

### Rank movements (raw → normalized): 19 of 40 projects move

- ▼4 `prj_28` (Flat Meadow): raw =24 → normalized 28
- ▼3 `prj_02` (Small Meadow): raw =16 → normalized 19
- ▼3 `prj_32` (Loud Ledger): raw =24 → normalized 27
- ▼2 `prj_03` (Deep Compass): raw =30 → normalized 32
- ▼2 `prj_06` (Dry Compass): raw =35 → normalized 37
- ▼2 `prj_19` (Small Relay): raw =13 → normalized 15
- ▼2 `prj_27` (Flat Thread): raw =24 → normalized 26
- ▼2 `prj_31` (Salt Ferry): raw =16 → normalized 18
- ▼2 `prj_39` (Paper Anchor): raw =20 → normalized 22
- ▼1 `prj_01` (Glass Signal): raw =24 → normalized 25
- ▼1 `prj_04` (Green Switch): raw =11 → normalized 12
- ▼1 `prj_05` (North Compass): raw =39 → normalized 40
- ▼1 `prj_11` (Salt Ledger): raw =1 → normalized 2
- ▼1 `prj_13` (Quiet Anchor): raw =30 → normalized 31
- ▼1 `prj_16` (Salt Kiln): raw =6 → normalized 7
- ▼1 `prj_22` (Dry Bridge): raw =35 → normalized 36
- ▼1 `prj_24` (Glass Beacon): raw =20 → normalized 21
- ▼1 `prj_26` (Amber Hours): raw =33 → normalized 34
- ▼1 `prj_36` (Salt Drift): raw =13 → normalized 14

### Spread before/after: 8.09 → 7.86

Sigma of per-judge mean scores moves from 8.09 raw to 7.86 after offset removal: with the adaptive choice (near-maximal shrinkage) the fitted offsets are close to zero, so almost nothing is removed. The raw spread of judge means on this sparse fixture mostly reflects which projects each judge happened to receive, not an estimable judge level — consistent with the leave-one-out and permutation results below.

### Judge spread, organizers' definition

Sample SD of per-judge mean scores (mean of the three 1–5 criteria per review): 0.4198 over all 126 reviews and 30 judges (this matches the homepage σ = 0.42 confirmed by the organizers) → 0.3292 after the duplicate policy excludes `prj_07`'s reviews (121 reviews, 29 judges) → 0.3201 after offset removal at the CV-chosen λ=100 (λ=0: 0.6145; λ=2: 0.1908).

The first drop comes from removing the single all-2s review on the superseded project, the second step removes the fitted judge levels at each stated shrinkage, and a smaller spread after that removal is not itself evidence that the ranking improved — the check for improvement is held-out prediction, the permutation test and the simulations, not the spread.

### The judge who marks everything the same

`jdg_07` (Iva Petrova) gave 3 reviews with identical criterion values everywhere (all 4s, score 75.00). The model absorbs that level into their offset (+0.30, labelled typical) and their reviews add no ordering information: they only pull their three projects toward a common value. They are kept by default and shown with this explanation; an organizer may exclude them with a recorded reason before publication.
Excluding `jdg_07` changes the normalized rank of 18 project(s): `prj_01` (Glass Signal: 25 → 22), `prj_02` (Small Meadow: 19 → 16), `prj_05` (North Compass: 40 → =39), `prj_09` (Hollow Signal: =16 → =28), `prj_12` (Open Beacon: 24 → 21), `prj_14` (Green Lantern: 29 → 26), `prj_17` (Small Loom: =16 → =28), `prj_18` (Open Kiln: 20 → 17), `prj_19` (Small Relay: 15 → 30), `prj_23` (Slow Quarry: 39 → =39), `prj_24` (Glass Beacon: 21 → 18), `prj_27` (Flat Thread: 26 → 24), `prj_28` (Flat Meadow: 28 → 25), `prj_29` (Flat Relay: 30 → 27), `prj_31` (Salt Ferry: 18 → 15), `prj_32` (Loud Ledger: 27 → 23), `prj_35` (Warm Beacon: 23 → 20), `prj_39` (Paper Anchor: 22 → 19).

### Outliers (|residual| > 2.5 x SD, projects with >= 3 reviews)

- `jdg_04` (Noor Haddad) scored `prj_37` (Salt Loom) 35.3 points below consensus (score 41.67).

Outliers are detection-only signals for organizers (possible collusion or undeclared conflict) and are never auto-excluded.

### Adaptive lambda selection (seeded 5-fold CV)

The predeclared procedure shuffles the 121 included reviews by sorted review id with seed `verdict`, holds out each fifth in turn, refits on the rest, and predicts held-out scores as `mu_p + b_j` (119 predicted, 2 skipped where the project or judge has no training review). The baseline predicts the training mean of the held-out review's project mates. Winner is the smallest CV RMSE; ties go to the larger lambda. Selected: `lambda = 100`.

| Predictor | CV RMSE |
|---|---:|
| project mean only | 19.52 |
| additive λ=0.5 | 23.01 |
| additive λ=1 | 21.80 |
| additive λ=2 | 20.83 |
| additive λ=5 | 20.00 |
| additive λ=10 | 19.70 |
| additive λ=20 | 19.58 |
| additive λ=50 | 19.53 |
| additive λ=100 | 19.52 |

### Lambda sensitivity (vs lambda = 2)

| λ | Top-5 (normalized) | Spearman ρ vs λ=2 |
|---:|---|---:|
| 0 | `prj_11`, `prj_25`, `prj_16`, `prj_21`, `prj_38` | 0.8313 |
| 1 | `prj_11`, `prj_34`, `prj_25`, `prj_16`, `prj_37` | 0.9944 |
| 2 | `prj_34`, `prj_11`, `prj_25`, `prj_37`, `prj_16` | 1.0000 |
| 5 | `prj_34`, `prj_11`, `prj_25`, `prj_37`, `prj_10` | 0.9936 |

Judge–project graph components: 1 (cross-component comparisons would be flagged weakly supported). Under-reviewed (< 3): `prj_10`, `prj_15`, `prj_18`, `prj_19`, `prj_24`, `prj_29`, `prj_39`, `prj_40`. Single-review judges (included reviews): `jdg_12`, `jdg_23`. Derived Bradley–Terry cross-check top-5: `prj_34`, `prj_33`, `prj_37`, `prj_02`, `prj_11`.

## Simulations

Design: the fixture's exact judge–project review pattern (121 pairs). Per replication: true project quality ~ N(0,1), judge offsets ~ N(0, σ_b) with σ_b in {0.0, 0.3, 0.6, 1.0} score points on the 1–5 scale (σ_b = 0.0 is the fair-judge control: no systematic judge bias, so any gap to raw means is the cost of normalizing), noise ~ N(0, 0.5) per criterion, rounded and clipped to integer 1–5; one constant judge (`jdg_07`, all 4s) like the fixture. 100 replications per σ_b. Methods: raw mean, per-judge z-score (zero-variance judges skipped), additive λ=0, additive λ=2, derived Bradley–Terry, plus adaptive (the same predeclared 5-fold CV rule over the default grid, applied inside each replication; 20 replications per σ_b for this method only, to keep the total runtime under 3 minutes). Reported: mean Spearman ρ with truth, top-5 recall, share of replications where the true best project ranks first.

### σ_b = 0.0 (seed 4404)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.958 | 0.846 | 0.660 |
| zscore | 0.871 | 0.652 | 0.250 |
| add0 | 0.887 | 0.670 | 0.440 |
| add2 | 0.959 | 0.830 | 0.600 |
| bt | 0.899 | 0.628 | 0.320 |
| adaptive (20 reps) | 0.961 | 0.840 | 0.550 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

### σ_b = 0.3 (seed 1101)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.940 | 0.804 | 0.590 |
| zscore | 0.879 | 0.672 | 0.250 |
| add0 | 0.894 | 0.726 | 0.400 |
| add2 | 0.950 | 0.818 | 0.570 |
| bt | 0.907 | 0.678 | 0.290 |
| adaptive (20 reps) | 0.954 | 0.820 | 0.500 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

### σ_b = 0.6 (seed 2202)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.903 | 0.726 | 0.530 |
| zscore | 0.877 | 0.608 | 0.290 |
| add0 | 0.878 | 0.648 | 0.410 |
| add2 | 0.930 | 0.748 | 0.550 |
| bt | 0.901 | 0.630 | 0.370 |
| adaptive (20 reps) | 0.943 | 0.830 | 0.500 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

### σ_b = 1.0 (seed 3303)

| Method | Mean ρ | Top-5 recall | Best ranked first |
|---|---:|---:|---:|
| raw | 0.816 | 0.578 | 0.380 |
| zscore | 0.868 | 0.622 | 0.330 |
| add0 | 0.849 | 0.642 | 0.420 |
| add2 | 0.877 | 0.662 | 0.520 |
| bt | 0.895 | 0.648 | 0.310 |
| adaptive (20 reps) | 0.906 | 0.680 | 0.600 |

Zero-variance judges skipped in 100.0% of replications (the forced constant judge is skipped every replication; natural zero-variance judges are rare).

Reading the tables in plain language: with fair judges (σ_b = 0.0) normalization costs essentially nothing — additive λ=2 matches raw means on rank correlation — so shrinkage is cheap insurance. Once judges disagree (σ_b ≥ 0.3), λ=2 beats raw means on both average rank correlation and top-5 recovery in every biased setting, while the unshrunk λ=0 fit overfits the sparse fixture pattern (it also predicts held-out fixture reviews worst in the leave-one-out check below). Per-judge z-scores throw away level information and must skip the constant judge in every replication, so they trail λ=2 in every setting and cannot use the constant judge's reviews at all. The adaptive rows (20 replications each, so noisier than the 100-rep rows) match or slightly beat fixed λ=2 on mean rank correlation and top-5 recall in every setting. Its mean chosen λ falls as judge bias grows (0.0: 44.9, 0.3: 1.3, 0.6: 0.6, 1.0: 0.5), so the rule normalizes gently when judges agree and strongly when they do not. Its best-first shares trail fixed λ=2 in three of four settings, which is within the wider sampling noise of the 20-rep subset.

### Designing for calibration

Same review budget (121 reviews): the fixture's review pattern vs an anchor pattern built by the portal assignment algorithm (`judging.assign.propose_assignments` with `anchors_per_track = 1`, target 3, seed `verdict`). The anchor run proposes 125 reviews across 8 anchor projects (`prj_14`, `prj_15`, `prj_17`, `prj_26`, `prj_28`, `prj_33`, `prj_34`, `prj_39`) with 39 anchor reviews; 4 project(s) come out under target because anchor load counts toward max_load, exactly the infeasibility the algorithm reports. Non-anchor reviews are then dropped from the most-covered projects until the total is back to 121, so the comparison holds the budget fixed. The anchor pattern covers 38 of 40 projects (uncovered: `prj_36`, `prj_40`).

| Design | Reviews | Median SE | Power at 8 pts |
|---|---:|---:|---:|
| fixture | 121 | 4.66 | 0.134 |
| anchor (1/track) | 121 | 4.67 | 0.127 |

Estimability uses `results.engine.estimability` with the defaults (additive model, lam = 2.0, noise 15.0, 200 reps, same seed for both designs): per-judge expected SE of the offset and the mean share of judges whose injected 8-point bias exceeds 2 SE.

Rank recovery under judge bias σ_b = 0.6 (100 replications, seed 2202, same generative model as above; projects with no reviews rank tied last):

| Design | Raw mean ρ | Additive λ=2 ρ |
|---|---:|---:|
| fixture | 0.903 | 0.930 |
| anchor (1/track) | 0.802 | 0.824 |

Reading: at this budget the anchor pattern does not improve average detectability (0.127 vs 0.134 for the fixture pattern; median SE 4.67 vs 4.66, within sampling noise). The anchor reviews concentrate on 8 projects while 2 project(s) lose coverage entirely, so rank recovery at σ_b = 0.6 is lower on the anchor pattern (additive ρ 0.824 vs 0.930). Anchors buy shared comparisons for the covered projects at the price of thinner coverage elsewhere — under a fixed budget the net effect here is nil to negative, which is itself the design-time lesson: check the estimability meter before buying anchors.

### How many reviews does normalization need?

Balanced random designs with 30 judges and 40 projects (same id sets, track-agnostic; `results.engine.review_budget_curve` with seed `verdict-budget`, 200 reps, additive λ=2.0), noise σ=10.92 estimated from the fixture's included reviews (pooled residual SD of the additive fit). Each row reports the median expected offset SE and the mean share of judges whose injected bias is detected:

| Reviews per judge | Median SE | Power at 8 pts | Power at 12 pts |
|---:|---:|---:|---:|
| 3 | 3.58 | 0.150 | 0.284 |
| 4 | 3.72 | 0.208 | 0.404 |
| 6 | 3.48 | 0.340 | 0.636 |
| 8 | 3.17 | 0.463 | 0.799 |
| 10 | 2.89 | 0.566 | 0.895 |
| 12 | 2.76 | 0.647 | 0.937 |
| 16 | 2.40 | 0.801 | 0.987 |

In plain language: at ~4 reviews per judge an 8-point harsh judge is caught ~21% of the time; ≈16 reviews per judge reaches 80%.

### Leave-one-review-out cross-validation (predicting unseen reviews)

Each included review was held out once: the model was refit without it and the held-out score predicted as `mu_p + b_j`; the baseline predicts the mean of the held-out review's project mates. 119 of 121 reviews predicted, 2 skipped (single-review judges `jdg_12`/`jdg_23`: holding out their only review leaves no data to estimate that judge's offset). Grids cover the project-mean baseline and additive λ ∈ {0, 1, 2, 5, 10}.

| Predictor | RMSE | MAE |
|---|---:|---:|
| project mean only | 19.28 | 15.54 |
| additive λ=0 | 22.57 | 18.40 |
| additive λ=1 | 20.44 | 16.44 |
| additive λ=2 | 19.86 | 15.81 |
| additive λ=5 | 19.33 | 15.55 |
| additive λ=10 | 19.17 | 15.47 |

Smallest unseen-review RMSE is λ=10 (19.17); λ=2 (19.86) is 0.58 points worse than the project mean (19.28), while λ=0 (22.57) is 3.29 points worse than ignoring judges entirely. In plain language: λ=2 predicts unseen fixture reviews worse than the project mean; strong shrinkage (λ≈10) is best; the permutation test finds no detectable judge effect in this sparse fixture; the adaptive rule therefore normalizes gently here and strongly when bias is present (simulations). The adaptive 5-fold choice on this fixture is λ=100 (CV RMSE 19.5211 vs baseline 19.5186: the best of the grid and within 0.01 of the project mean), i.e. near-maximal shrinkage, exactly as that reading prescribes.

### Permutation test for judge effects

Statistic: population variance of the fitted judge offsets (λ=2). Judge labels were shuffled 2,000 times within each track (seed 97531; each review keeps its project and score, only the judge label moves). Observed variance 24.40; null quantiles 5% 14.73, 25% 19.52, 50% 22.69, 75% 26.28, 95% 32.12, 99% 35.85; p = 0.381 (fraction of null draws at or above observed).

The test does not reject the null: with about three reviews per project the fitted offsets are mostly sampling noise, and random relabelings produce as much spread as the real labels. The test has low power on this sparse design — it guards against strong systematic effects rather than proving none — so the positive case for shrinkage rests on the leave-one-out check and the simulations above.

### Agreement between the normalized and Bradley–Terry rankings

Spearman ρ = 0.8509, Kendall τ = 0.6684 over the 40 projects ranked by both methods (score-level correlation, then rank positions). 15 project(s) differ by more than 5 places:

- `prj_02` (Small Meadow): normalized 19 vs Bradley–Terry 4
- `prj_03` (Deep Compass): normalized 32 vs Bradley–Terry 20
- `prj_19` (Small Relay): normalized 15 vs Bradley–Terry 27
- `prj_36` (Salt Drift): normalized 14 vs Bradley–Terry 26
- `prj_12` (Open Beacon): normalized 24 vs Bradley–Terry 13
- `prj_14` (Green Lantern): normalized 29 vs Bradley–Terry 19
- `prj_09` (Hollow Signal): normalized =16 vs Bradley–Terry 7
- `prj_18` (Open Kiln): normalized 20 vs Bradley–Terry 28
- `prj_27` (Flat Thread): normalized 26 vs Bradley–Terry 18
- `prj_10` (Still Beacon): normalized 3 vs Bradley–Terry 10
- `prj_21` (Copper Kiln): normalized 8 vs Bradley–Terry 15
- `prj_31` (Salt Ferry): normalized 18 vs Bradley–Terry =11
- `prj_35` (Warm Beacon): normalized 23 vs Bradley–Terry 30
- `prj_17` (Small Loom): normalized =16 vs Bradley–Terry =22
- `prj_28` (Flat Meadow): normalized 28 vs Bradley–Terry 34

The Bradley–Terry cross-check uses only within-judge orderings (derived pairwise comparisons), so judge levels cancel out of it entirely; its broad agreement with the additive ranking is independent evidence that the offsets removed are level, not order.

### Robustness of the fixture result (adaptive lambda = 100)

Winner `prj_34` (Iron Switch); top-3: `prj_34` (Iron Switch), `prj_11` (Salt Ledger), `prj_10` (Still Beacon). The winner leads the runner-up by 0.09 normalized points. Every refit below reuses the already-chosen `lambda = 100` (no lambda re-selection: the certificate is about the published ranking) and warm-starts from the full-data fit; iteration is in sorted-id order, so the certificate is deterministic.

Leave-one-judge-out (29 judges with included reviews): 1st place holds in 24 of 29 removals; the top-3 set holds in 22 of 29. 5 removal(s) change the winner:

- without `jdg_04` (Noor Haddad): 1st goes to `prj_37` (Salt Loom); new top-3: `prj_37`, `prj_34`, `prj_11`
- without `jdg_15` (Yuki Sato): 1st goes to `prj_11` (Salt Ledger); new top-3: `prj_11`, `prj_25`, `prj_37`
- without `jdg_24` (Diego Herrera): 1st goes to `prj_18` (Open Kiln); new top-3: `prj_18`, `prj_34`, `prj_11`
- without `jdg_25` (Thandi Dlamini): 1st goes to `prj_11` (Salt Ledger); new top-3: `prj_11`, `prj_25`, `prj_34`
- without `jdg_29` (Ines Rocha): 1st goes to `prj_10` (Still Beacon); new top-3: `prj_10`, `prj_34`, `prj_11`

Leave-one-review-out (121 included reviews): 1st place holds in 115 of 121 removals (6 flip it):

- without `jdg_04__prj_37` (Noor Haddad on `prj_37` (Salt Loom)): 1st goes to `prj_37` (Salt Loom)
- without `jdg_15__prj_34` (Yuki Sato on `prj_34` (Iron Switch)): 1st goes to `prj_11` (Salt Ledger)
- without `jdg_24__prj_18` (Diego Herrera on `prj_18` (Open Kiln)): 1st goes to `prj_18` (Open Kiln)
- without `jdg_25__prj_11` (Thandi Dlamini on `prj_11` (Salt Ledger)): 1st goes to `prj_11` (Salt Ledger)
- without `jdg_25__prj_25` (Thandi Dlamini on `prj_25` (Dry Relay)): 1st goes to `prj_25` (Dry Relay)
- without `jdg_29__prj_10` (Ines Rocha on `prj_10` (Still Beacon)): 1st goes to `prj_10` (Still Beacon)

Flip margin: moving 1 review of `prj_34` to the rubric midpoint (score 50) flips 1st place: `jdg_15__prj_34`.

In plain language: 1st place (prj_34) holds in 24 of 29 single-judge removals; the top-3 set holds in 22 of 29; without jdg_04 1st goes to prj_37; without jdg_15 1st goes to prj_11; without jdg_24 1st goes to prj_18; without jdg_25 1st goes to prj_11; without jdg_29 1st goes to prj_10. 1st place (prj_34) holds in 115 of 121 single-review removals; flipping removals: jdg_04__prj_37 -> prj_37, jdg_15__prj_34 -> prj_11, jdg_24__prj_18 -> prj_18, jdg_25__prj_11 -> prj_11, jdg_25__prj_25 -> prj_25, jdg_29__prj_10 -> prj_10. Moving 1 review of prj_34 to the rubric midpoint (50) flips 1st place (review: jdg_15__prj_34).

Reading: with a 0.09-point lead the fixture winner is fragile — 5 judges and 6 single reviews can each flip it, and moving its single most favourable review to the midpoint is enough. That is the honest consequence of a near-tie at the top, not a flaw in the fit: the certificate reuses the published lambda and shows exactly where the result could break.

## Properties

Shift invariance: adding +1 to every criterion value of one judge (`jdg_07`, chosen because 4+1 needs no clipping) moves the raw ranking but leaves the additive (λ=0) ranking exactly unchanged — the shift is absorbed by that judge's offset:
- raw ranking positions changed: 17 of 40;
- additive (λ=0) rankings identical: True.

Constant-judge case: a judge with zero variance breaks per-judge z-scores (division by zero) and their scores depend on which projects they happened to receive; the additive model instead absorbs their level into the offset (+0.30) and their reviews contribute no ordering information. That is why the portal uses offsets, not z-scores (see JUDGING.md).

## Method lineage

The additive fit is a shrinkage-penalized least-squares estimator of a two-way layout, i.e. the Henderson BLUP / linear mixed-model solution in which the penalty λ plays the role of the variance ratio σ²_error / σ²_judge: larger λ trusts the judge sample less. Rater-severity modelling of the same form is the workhorse of Many-Facet Rasch measurement (Linacre), and review-score calibration of this kind was studied for peer review by Ge, Welling and Ghahramani (2013). Linear-bias models have known limits — Wang and Shah (2019) show where they break under strategic or correlated miscalibration, which is why the proof reports outliers and states the offset-only limitation instead of claiming more. The Bradley–Terry cross-check is fitted by the Hunter (2004) MM algorithm, whose fixed point on the virtual-opponent-augmented (hence strongly connected) graph is the exact MAP estimate.

## Limitations

- Offset-only model: linear level habits are removed, but scale habits (harsh on weak projects, generous on strong ones) and nonlinear mappings are not modelled.
- Few reviews per judge: single-review offsets are almost pure shrinkage toward zero; their projects are ranked mostly by raw means.
- Clipping at the scale ends (1 and 5) destroys level information a shift would otherwise preserve.
- Correlated or strategic bias (vote-trading, team-targeted collusion) is not addressed by normalization; see the outlier list and THREAT-MODEL.md.

## Reproducibility

Regenerate with `.venv\Scripts\python.exe scripts/normalization_proof.py` (standard library only; reads `fixtures.json`, imports `src/results/engine.py`). Simulation seeds: σ_b=0.0 → 4404, σ_b=0.3 → 1101, σ_b=0.6 → 2202, σ_b=1.0 → 3303. Adaptive rule: grid λ ∈ {0.5, 1, 2, 5, 10, 20, 50, 100}, 5 folds, seed `verdict`; adaptive simulation rows use 20 replications per σ_b (other rows 100). Leave-one-out grid: λ ∈ {0, 1, 2, 5, 10}. Permutation test: 2,000 within-track shuffles, seed 97531, λ=2. Calibration section: anchor pattern (anchors_per_track=1, target=3, seed `verdict`, trimmed to 121 reviews), estimability seed `verdict-cal` (200 reps), rank recovery σ_b=0.6 seed 2202 (100 reps). Budget planner: 30 judges / 40 projects, grid {3, 4, 6, 8, 10, 12, 16}, bias 8, σ estimated from the fixture, 200 reps, seed `verdict-budget`. No timestamps are written, so regenerating twice gives identical bytes.
