SELECT set_type, COUNT(*) AS n
FROM {{ ref('stg_sets') }}
WHERE set_type NOT IN ('normal', 'failure', 'dropset')
GROUP BY 1
