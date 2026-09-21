select application_id from {{ ref('stg_outcomes') }}
where consumer_id is null or currency is null or decided_at is null
   or settled_minor is null or attempted_minor is null or returned_minor is null or recovered_minor is null
   or outstanding_minor is null or return_count is null or payment_count is null
union all
select application_id from {{ ref('stg_features') }}
where consumer_id is null or decided_at is null or history_missing is null
   or original_approved is null or split is null
union all
select application_id from {{ ref('stg_scores') }}
where feature_version is null or data_cutoff is null or scored_at is null or score_context is null or model_version is null or split is null or consumer_seen_in_train is null
