select application_id from {{ ref('stg_outcomes') }}
where settled_minor < 0 or settled_minor > attempted_minor or attempted_minor < 0 or returned_minor < 0 or recovered_minor < 0
   or outstanding_minor < 0 or returned_minor > settled_minor
   or recovered_minor > returned_minor
   or outstanding_minor <> returned_minor - recovered_minor
   or return_count < 0 or payment_count < 0 or return_count > payment_count
   or mature is null or observed is null
