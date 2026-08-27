# Operations Page

The store-ops write surface — Dana works the shortfall backlog, the agent's recovery actions land in real time. This is the **Visualize** layer of the enablement arc, and the surface the **Act** layer writes to.

> **Design the page from the persona, not the template.** Dana stares at *stores on a map* all day — where's short, where's over-stocked. The primary visualization is therefore a **US store map** (red stockouts / amber overstock on the affected SKUs), NOT a bare table. The queue is the secondary, drill-in surface. If the screenshot would read as "a table with rows", redesign until it reads as "this is a store-ops app" at a glance.

## Layout

**Header:** "Work the shortfall backlog." / "Every red store is a top product a customer came in for and left without. Every amber store is margin about to be discounted away."

**"Ask the assistant" banner:** Sparkle-icon card — "Ask why a store is short and get the recovery move" → opens the dock with the Store 214 starter.

**KPI cards (3 across):**
- **Lost-sales exposure** ($, red tint) — from the exposure metric view over the current open shortfalls.
- **Markdown exposure** ($, amber tint) — the southern surplus at risk.
- **Open shortfalls** (#, neutral) — count of `stockout`/`at_risk` positions. Ticks down live when the agent acts.

**Store map** (the hero visual, between the KPIs and the queue): a US map with one marker per affected store, colored by `position_status` — **red** for northern stockouts, **amber** for southern overstock, steel for healthy. Size by recent velocity. Store 214 (Denver) is the demo's zoom target. Clicking a marker filters the queue to that store. (Reuse the template's `CityMap` bubble-map component — same react-leaflet plumbing, recolored/rekeyed to stores + `position_status`.)

**Shortfall queue:** Filterable, sortable table.
- Status tabs: All / Stockout / At risk / Recovery in progress
- Search: free-text across store name, city, SKU, product
- Climate-zone filter chip (North / South / Mixed), Category filter chip
- Sortable columns: **Lost-sales exposure** ($), **Recent velocity** (units/day)
- Columns: Store (name + city) | Product | On hand | Velocity (7d) | **Weeks of supply** | **Exposure** ($) | **Recommended move** (badge: Transfer / Expedite / Substitute — from the model) | Status
- Recovery-move badge variants: `transfer` (solid) | `expedite` (outline) | `substitute` (muted); shown once the model has scored the shortfall.
- Click row → detail drawer.

**Detail drawer (right slide-over, ~60% width).**
- **Shortfall tab** — detail grid (store, SKU, on_hand, velocity, weeks_of_supply, lost-sales exposure) + the nearest-surplus panel (source store, its on-hand, distance) + **the ranked recovery options** (transfer/expedite/substitute, each with units, cost, margin impact, predicted recaptured $) with **Approve recommended move / Override** buttons. **For the substitute option:** a small **product search box** below the option ("Find a comparable in-stock item") powers a lightweight search over the product catalog using Lakebase Search (Milestone 2 Lakebase work) — returns ranked candidate SKUs with name, category, price, and on-hand so the agent can suggest a contextual substitute.
- **Store tab** — store profile (region, climate zone, format, foot traffic) + recent sell-through sparkline on this SKU.
- **Activity tab** — merged timeline (agent audit trail + approved actions with timestamps + who approved).

## NorthPeak data

The queue reads from Lakebase `app.store_sku_position` (synced, read-only) filtered to open shortfalls, LEFT JOIN `app.recovery_recommendations` (the ranked recovery move per shortfall). The map reads the same position rows with store coordinates. ~30 northern stockout positions + ~40 southern overstock positions on the 5 affected SKUs; a few hundred everyday positions in the background so the affected ones stand out.

The **Act** write lands in `app.ops_actions` (writable) — the synced position table is read-only, so an approved transfer is recorded as an action row (move_type, from/to store, units, drafted request, predicted recaptured $, status, approved_by), and the queue derives "recovery in progress" by joining position → its latest `ops_action`. The KPI exposure numbers recompute as shortfalls gain a recovery action. See `03_DATA_MODEL.md`.
