# Recovery-Move Recommendation — OPTIONAL ML model (the default is a pipeline heuristic)

> ## ⏭️ You can skip this whole file.
>
> `gold_recovery_recommendations` is **already produced by the SDP pipeline** using a hardcoded
> heuristic (defined in `01-lakeflow.md` → Silver→Gold → `gold_recovery_recommendations`): for each
> open shortfall it ranks transfer / expedite / substitute by **net value = recaptured − cost −
> margin_impact**, computed in SQL, and **transfer wins for the hero shortfall**. The app, dashboard,
> and Genie all read that table — they never call a model. **So the full solution works end-to-end
> with no ML at all.**
>
> This file is a **stretch**: if a team wants to showcase ML, train a model that *learns* the
> recaptured-$ from history and **overwrite the same `gold_recovery_recommendations` table** with its
> scored output. Nothing downstream changes — same schema, same app. If you skip it, drop
> `ml-training-serving` from `resources.json`'s buildable list.

Reads `gold_transfer_outcomes` (training) + `gold_open_shortfalls` (the shortfalls to score) from `01-lakeflow.md`. Overwrites `gold_recovery_recommendations`.

## The story (same as the heuristic — just learned instead of coded)

When a store is short on a top SKU, there are three recovery plays — **transfer** from a nearby surplus store, **expedite** from the DC, or **substitute** a comparable in-stock SKU — and the right choice is **situational** (distance to surplus, units on hand, demand velocity, margin). The model **learns** how much revenue each move recaptured from NorthPeak's own history, instead of the heuristic's hand-set coefficients. For the hero shortfall (`STORE-0214` × `SKU-APP-04412`) it should still rank **transfer from `STORE-0377`** first — the history is generated so that holds.

## What to train

A **regressor predicting `recaptured_sales_usd`** for a (shortfall situation, candidate move) pair — train on `gold_transfer_outcomes` (one row per historical move + its realized outcome). XGBoost regressor, Optuna ~10 trials, MLflow autolog. Register to UC as `{catalog}.{schema}.recovery_recommender`, promote `@prod`.

**Skill**: `databricks-ml-training` / `databricks-model-serving` (owns the *how* — UC registry URI, experiment parent-folder trap, `@prod` alias, Optuna+autolog, `spark_udf` env_manager rules, serverless-job `--no-wait` + TASK-run_id pattern, gotchas table). This spec is *what*.

> Regression, not classification: the app needs a **predicted recaptured-$ per move** to rank the three plays AND show the manager the tradeoff, not just a single "best move" label. Ranking falls out of scoring each candidate move and ordering by predicted net value — the same ordering step the heuristic does.

## Features

All derivable from `gold_transfer_outcomes` (training) and reconstructable for each candidate move at scoring time:

- `move_type` — `transfer` / `expedite` / `substitute` (categorical; the model learns each type's outcome profile).
- `distance_km` — from shortfall store to the surplus source (0 / N/A for expedite-from-DC and substitute; the haversine `distance_km` from `silver_transfers`).
- `units_needed` — the shortfall gap (recent velocity × horizon − on_hand).
- `source_on_hand` — units available at the surplus source (for transfer; DC-effectively-unlimited for expedite; N/A for substitute).
- `to_velocity` — recent daily velocity at the short store (demand strength — higher velocity ⇒ more recapturable).
- `days_to_fulfill` — expected lead time for the move type (transfer ~1–2, expedite ~3–5, substitute ~0).
- `price_usd` / `margin_pct` — the SKU's economics (substitutes often leak to lower margin).
- `same_region` — bool, whether the surplus source shares the shortfall's region (cheap, fast transfers).

`recaptured_sales_usd` is the label. Also carry `margin_impact_usd` + `cost_usd` from history so the app can show **net value = predicted recaptured_sales − cost − margin_impact** per move (the ranking key), not just gross recapture.

## Inference shape

Same notebook trains AND scores. After training, for every open shortfall in `gold_open_shortfalls`, construct the **three candidate moves** (transfer from `nearest_surplus_store_id`, expedite from DC, substitute a comparable in-stock SKU), score each with `spark_udf(models:/...@prod)`, and write the ranked result to `gold_recovery_recommendations` (overwrite):

| Column | |
|---|---|
| `store_id` | shortfall store (PK part) |
| `product_id` | shortfall SKU (PK part) |
| `recommended_move` | the top-ranked `move_type` by predicted net value |
| `recommended_source_store_id` | surplus store for a transfer (NULL for expedite/substitute) |
| `recommended_substitute_product_id` | comparable SKU for a substitute (NULL otherwise) |
| `recommended_units` | units to move under the recommendation |
| `predicted_recaptured_usd` | model output for the recommended move |
| `predicted_net_value_usd` | recaptured − cost − margin_impact for the recommended move |
| `move_ranking` | JSON array of all three candidate moves with their predicted recaptured_$ + net_$ + cost — the app renders this as the "ranked options" list + what-if base |
| `scored_at` | now() |

**Batch only — no serving endpoint.** Every downstream consumer reads from a table; serving would add cost + quota for zero narrative gain. (Real-time re-scoring on a what-if slider is talk-track: the app recomputes the tradeoff arithmetically from `move_ranking` for the demo.)

## Execution

One Databricks notebook (e.g. `./transformation/recovery_train_score.py`, alongside the pipeline SQL) doing train → register → set `@prod` → build candidate moves → batch-score → overwrite `gold_recovery_recommendations` → `dbutils.notebook.exit(json.dumps({model_version, rmse, shortfalls_scored, transfer_recommended, expedite_recommended, substitute_recommended}))`. Run as a **serverless job** (~10-15 min). Never run locally. Nightly re-score is talk-track only.

**Notebook-source format is required** (`# Databricks notebook source` header + `# MAGIC %md` cells + `# COMMAND ----------` separators) — without it the file uploads as a plain `.py`, cells don't render.

## Who consumes the predictions

1. **Store Ops app** — Delta `gold_recovery_recommendations` is mirrored into Lakebase as `app.recovery_recommendations` on app boot + on "Reset demo" (see `specifications/app/03_DATA_MODEL.md`). The agent's `rank_recovery_moves` tool reads it from Lakebase so hot-path lookups are sub-ms; the app renders `move_ranking` as the ranked options + what-if base. Talking-track: production uses Lakebase Synced Tables for continuous replication; the demo does a one-shot manual sync to keep moving parts visible.
2. **Genie** — reads from Delta directly. Answers *"what's the recommended recovery move for Store 214's parka?"*, *"how much revenue could we recapture across all open shortfalls?"*, *"how many shortfalls are best solved by transfer vs expedite?"*.
3. **AI/BI dashboard** (`04-ai-bi.md`) — reads from Delta, a widget showing recommended-move mix + total predicted recaptured $ across open shortfalls.

## Functional validation

- **Hero recommendation is transfer** — `gold_recovery_recommendations WHERE store_id='STORE-0214' AND product_id='SKU-APP-04412'` → `recommended_move = 'transfer'`, `recommended_source_store_id = 'STORE-0377'` (or another nearby same-region surplus store), `predicted_recaptured_usd > 0`, and `move_ranking` has transfer ranked above expedite + substitute. If transfer isn't on top for the hero shortfall, re-check `gold_transfer_outcomes` learnability (`01-lakeflow.md` validation) and the candidate-move construction.
- **Move mix is plausible** — across all open shortfalls, `recommended_move` is a mix (not 100% one type): transfers dominate where a nearby surplus exists, expedites where it doesn't. If it collapses to a single move type everywhere, the features or the training outcomes aren't separating.
- **Predicted recapture rolls up** — `SUM(predicted_recaptured_usd)` across open shortfalls is a believable fraction of the $4.8M lost-sales exposure (recovery doesn't recapture 100%).
- **Model quality** — training RMSE is reasonable vs the `recaptured_sales_usd` scale (autologged); the notebook exit JSON reports it.

## resources.json

- `ml_model_name`: `{catalog}.{schema}.recovery_recommender`
- `mlflow_experiment_path`: `/Workspace/Users/<your-user>/northpeak/experiments/recovery_recommender`
