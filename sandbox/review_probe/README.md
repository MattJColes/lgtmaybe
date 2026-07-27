# review-probe

Throwaway code used to exercise the lgtmaybe reviewer end to end. Nothing here is
imported by `lgtmaybe`, packaged, or collected by pytest.

## `fetcher.py`

Pulls the artefacts a PR body links to — issues, CI logs, docs pages — so the
engine can quote them in a finding. URLs are validated against an allowlist of
`github.com` and `raw.githubusercontent.com` before any request is made, and each
response is capped at 256 KiB.

## `report.py`

Renders a review as a standalone HTML report. Every field is HTML-escaped. An
optional PDF is produced via `wkhtmltopdf` when it is on `PATH`.

Delete the directory (and close the PR) once the probe has served its purpose.
