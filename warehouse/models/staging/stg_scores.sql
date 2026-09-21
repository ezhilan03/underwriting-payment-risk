select application_id, model_version, feature_version,
       cast(data_cutoff as timestamp) as data_cutoff,
       cast(scored_at as timestamp) as scored_at,
       score_context, risk_score, split, consumer_seen_in_train
from {{ source('risk_raw', 'scores') }}
