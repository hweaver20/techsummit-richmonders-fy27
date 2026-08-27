# transformation/

Put your **data transformation** here — the SDP (Spark Declarative Pipeline) SQL
that turns the raw parquet (in the `raw_data` volume, written by
`../data_generation/generate_data.py`) into the silver + gold tables described in
`../specifications/01-lakeflow.md` (`gold_store_sku_position`,
`gold_open_shortfalls`, `gold_transfer_outcomes`, `gold_recovery_recommendations`,
the `ai_classify` markdown signal, …).

This folder ships empty — building the pipeline is Milestone 1.
