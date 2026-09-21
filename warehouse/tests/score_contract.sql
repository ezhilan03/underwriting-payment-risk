select s.application_id from {{ ref('stg_scores') }} s
join {{ ref('stg_features') }} f using (application_id)
where s.risk_score < 0 or s.risk_score > 1 or s.risk_score is null
   or s.split <> f.split or s.data_cutoff <> f.decided_at
   or s.scored_at < s.data_cutoff or s.feature_version <> 'pit-v1'
   or s.score_context <> 'retrospective_synthetic_experiment'
union all
select application_id from {{ ref('stg_scores') }}
group by application_id, model_version having count(*) <> 1
