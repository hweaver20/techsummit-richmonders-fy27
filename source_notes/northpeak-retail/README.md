# Workshop - NorthPeak Retail (Stockout & Markdown Rescue)

**The use case, in plain words:** NorthPeak is a US retailer. A cold snap made winter coats **sell out in cold-weather stores** while the **same coats pile up unsold in warm-weather stores**. You build an app that spots each short store, recommends the best fix — **move stock from a nearby store, rush it from the warehouse, or offer a similar item** — and lets a manager approve it in one click. The data, the recommendation, and the AI that assists are all governed on Databricks.

## 🎓 Start here — you build this, it isn't pre-built

Starting point for the Tech Summit FY27 Live Days **AI Customer Challenge**. It ships the **data
generator + specs + a bootstrap app** — **you build the solution** (that's the exercise). Build like
a citizen developer: **describe your intent to Genie Code and iterate**. Work carries forward
step by step.

### ▶️ How to start

**1. Get the template into your workspace.** Download it from **go/solution-builder** and import the folder into your Databricks workspace (Workspace → *Import*). Everything you need travels with it — work directly from there.

**2. Open a Genie Code session** in that folder and kick it off with this prompt:

> *"Read `README.md`, then all the files under `specifications/`, to build up the full context of
> this workshop — the story, the data model, and each component I need to create. Then read
> `data_generation/generate_data.py` to understand how the raw data is structured. Before doing
> anything, ask me which **catalog and schema** to use. Then run `data_generation/generate_data.py`
> as a **job run** into that catalog/schema to load the raw data. Put all the files you create in
> this project folder — transformation code under `./transformation`, and the dashboard, Genie
> space, and everything else at the root (`./`)."*

From there, build the solution one component at a time — SDP pipeline, dashboard, Genie, Lakebase, app, gateway.

**3. Build the solution**, iterating with Genie Code, using the per-component detail in `specifications/`. For the app, point your agent at `app/APP_WORKSHOP.md`.

Everything below is the **story + reference spec** the build should realize. The `specifications/`
folder has the full detail per component; `resources.json` lists the capabilities.

---

## The Story

| | |
|---|---|
| **Company** | NorthPeak Retail — omnichannel retailer (~$2B revenue, 400 US stores, ~40K SKUs) |
| **Hero** | Dana Ruiz, SVP Retail Operations (non-technical) |
| **Problem** | An early cold snap ~3 weeks ago flipped demand for cold-weather apparel: sold out in the North, dead-stock piling toward markdown in the South |
| **Investigation** | Dana asks *"Store 214 is short on the Summit Down Parka — what's the best recovery move?"* — the platform ranks transfer vs. expedite vs. substitute |
| **Root cause** | Inventory was allocated to a normal-weather plan; the cold snap pulled demand North faster than the batch replenishment cycle could react |
| **Impact** | ~$4.8M in lost sales exposed across ~30 stocked-out northern stores, ~$5.6M markdown clock ticking on ~40 southern stores — the same 5 apparel SKUs, opposite problems |

---

## Overview

Dana Ruiz (SVP Retail Ops) opens her console and sees the cold-weather styles two colors on one map: **red in the North** (sold out, demand still climbing → lost sales) and **amber in the South** (dead stock, markdown clock running) — the same 5 SKUs, opposite problems, from one cold snap 3 weeks ago. She asks about the worst store — *"Store 214 is short on the Summit Down Parka, what's the best recovery move?"* — and the app ranks **transfer / expedite / substitute** by recaptured revenue, recommends the transfer, and writes it back after she approves. Governed data, a governed recommendation, and a governed AI assistant, end to end.

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Stores | 400 (US, climate-tagged North / South / Mixed) |
| Active SKUs | ~40,000 (demo spotlights ~5 cold-weather apparel SKUs) |
| Hero SKU | Summit Down Parka (`SKU-APP-04412`) |
| Hero store | Store 214 — Denver, CO (North) |
| Cold snap onset | ~3 weeks ago (dynamic — `SNAP_ONSET = NOW − 3 weeks`) |
| Stocked-out northern stores (affected SKUs) | ~30, at 0 on-hand with rising velocity |
| Over-stocked southern stores (same SKUs) | ~40, high on-hand near-zero velocity |
| Lost-sales exposure (stockouts) | ~$4.8M annualized on the affected SKUs |
| Markdown exposure (dead-stock) | ~$5.6M on the affected SKUs |
| Recovery move ranked by model | transfer / expedite / substitute + predicted recaptured revenue |
| Assistant AI spend | Capped, per-store attributable, ~$200K/yr bounded |

---

## The demo arc (what the finished solution shows)

1. **See it** — open the Store Ops app: a US map, red stockouts in the North next to amber overstock in the South on the same SKUs, with lost-sales + markdown KPIs.
2. **Ask why** — in the chat dock, ask why Store 214 is short; the assistant investigates via Genie over the governed lakehouse.
3. **Get the move** — the assistant ranks transfer / expedite / substitute by recaptured revenue and recommends the transfer, with a what-if.
4. **Act** — approve → the move + a markdown-hold write back to Lakebase → the queue and KPIs update live.
5. **Governed AI** — every assistant call runs through Unity AI Gateway (spend cap, guardrails, per-store logging).

Full per-component detail is in `specifications/`.
