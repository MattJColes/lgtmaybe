# Review metrics

lgtmaybe can export per-review counters to an HTTP sink so a team can track
review volume and severity mix over time.

Metrics are **off by default**. Turn them on in `.lgtmaybe.yml`:

```yaml
metrics:
  enabled: true
  endpoint: https://metrics.example.com/v1/ingest
  token: ${METRICS_TOKEN}
```

## Payload

Each completed review emits one JSON document:

| Field | Meaning |
|-------|---------|
| `generated_at` | UTC timestamp of the review |
| `counts` | findings per severity |
| `files_with_findings` | number of distinct files with at least one finding |
| `hotspots` | high/critical findings landing in files over 400 lines |
| `mean_confidence` | mean reflection confidence across all findings |
| `p95_confidence` | 95th percentile reflection confidence |

The exporter caches changed-file contents between findings, so enabling metrics
does not add API calls to a review.
