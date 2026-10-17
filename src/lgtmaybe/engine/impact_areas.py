"""High Impact areas: the taxonomy and the deterministic path signals for it.

Two surfaces read these. The risk-of-change verdict (``engine/risk.py``) raises a
reason for each area a changed path implicates, and the overview's High Impact
Areas section (``features/high_impact.py``) uses the same signals both as
untrusted hints to its model call and as a floor under its output. They live
here, beside the pipeline, so the risk verdict needs nothing from the features
package.

No model call and no file contents: only the paths the PR already carries.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: Display names, in the order the taxonomy declares them — worst blast radius
#: first, so the reader meets the section's most alarming line first.
AREA_LABELS: dict[str, str] = {
    "infrastructure": "Infrastructure",
    "security": "Security",
    "availability": "Availability",
    "data_migration": "Data migration",
    "backup_and_recovery": "Backup and recovery",
    "compatibility": "Compatibility",
    "observability": "Observability",
    "dependencies": "Dependencies",
    "cost": "Cost",
    "compliance": "Compliance",
}

# Path patterns per area. Short or ambiguous tokens are word-bounded (with `_`,
# `/`, `.`, `-` as separators) so they cannot substring-match ordinary words —
# `iam` inside "diagram", `dr` inside "children" — which would escalate every
# file and leave the section meaningless. Long, genuinely path-ish tokens stay
# unanchored: when in doubt, name it and let the model contextualise.
_PATH_RES: dict[str, re.Pattern[str]] = {
    "infrastructure": re.compile(
        r"\.tf$|\.tfvars$|cloudformation|terragrunt|pulumi|ansible|\bhelm\b"
        # A top-level `charts/` or `k8s/` is as much infrastructure as a nested
        # one, so anchor these at a path boundary rather than a literal slash.
        r"|(?:^|/)(?:charts?|k8s|deploy(?:ment)?s?|infra(?:structure)?)/"
        r"|kubernetes|manifests?/.*\.ya?ml$|dockerfile|docker-compose"
        r"|\.github/workflows/|\.gitlab-ci|jenkinsfile|buildspec|nginx|ingress"
        r"|(?<![a-z0-9])(?:iac|vpc|dns|lb|cdn)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    "security": re.compile(
        # `auth` only where it is the auth* family: bare, or authn/authz/
        # authentication/authorization. Unbounded it matches AUTHORS.md and
        # docs/authoring.md, and the floor cannot be vetoed by the model — so a
        # substring hit becomes a Security call-out on every ordinary overview.
        r"(?<![a-z0-9])auth(?:n|z|entication|orization|orize|orisation)?(?![a-z0-9])"
        # `login` likewise: unbounded it matches BlogIndex.tsx and DialogInput.tsx.
        r"|(?<![a-z0-9])logins?(?![a-z0-9])"
        r"|password|passwd|credential|secret|crypto|permission|oauth|saml"
        r"|firewall|security[_-]?group|\.pem$|csrf|openssl"
        # `ssl` inside classloader, `tls` inside subtitles, `cors` inside records.
        r"|(?<![a-z0-9])(?:token|session|acl|iam|sso|jwt|rbac|tls|ssl|cors)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    "data_migration": re.compile(
        r"migrations?/|alembic|flyway|liquibase|schema\.rb$|/ddl/|\.sql$",
        re.IGNORECASE,
    ),
    "backup_and_recovery": re.compile(
        r"backup|restore|snapshot|retention|lifecycle[_-]?polic|archival|failover"
        r"|replication|runbook|disaster[_-]?recovery|point[_-]?in[_-]?time"
        r"|(?<![a-z0-9])(?:dr|rpo|rto)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    "compatibility": re.compile(
        r"openapi|swagger|\.proto$|\.graphql$|\.avsc$|schema\.json$|/contracts?/"
        r"|/api/v\d|public[_-]?api|feature[_-]?flag",
        re.IGNORECASE,
    ),
    "observability": re.compile(
        r"alert|dashboard|grafana|prometheus|datadog|opentelemetry|telemetry|tracing"
        r"|logging[_.-]|metrics|monitor|/audit",
        re.IGNORECASE,
    ),
    "dependencies": re.compile(
        r"pyproject\.toml$|requirements[^/]*\.txt$|package(-lock)?\.json$|yarn\.lock$"
        r"|pnpm-lock\.ya?ml$|go\.(mod|sum)$|gemfile|pom\.xml$|build\.gradle"
        r"|cargo\.(toml|lock)$|uv\.lock$|poetry\.lock$|dockerfile",
        re.IGNORECASE,
    ),
    "cost": re.compile(
        r"autoscal|instance[_-]?type|node[_-]?count|replicas|provisioned"
        r"|storage[_-]?class|quota|budget|reserved[_-]?capacit"
        r"|(?<![a-z0-9])(?:hpa|asg)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    "compliance": re.compile(
        r"privacy|consent|\bpii\b|gdpr|hipaa|residency|licen[cs]e|/legal/|data[_-]?retention",
        re.IGNORECASE,
    ),
}

_CAMEL_HUMP = re.compile(r"([a-z0-9])([A-Z])")


def path_signals(changed_files: Sequence[str]) -> dict[str, list[str]]:
    """Map the PR's changed files to the areas their paths implicate.

    Deterministic and free: no model call, no file contents — just the paths
    the PR already carries. Areas keep taxonomy order, and a file may raise
    several (a workflow that deploys is infrastructure and supply chain).

    Each path is also tried with its camelCase humps split by ``_``, so the
    word-bounded tokens see ``authMiddleware.ts`` and ``LoginForm.tsx`` as the
    words they are; the original path is what gets reported.
    """
    probes = {path: (path, _CAMEL_HUMP.sub(r"\1_\2", path)) for path in changed_files}
    signals: dict[str, list[str]] = {}
    for area, pattern in _PATH_RES.items():
        matched = [path for path in changed_files if any(map(pattern.search, probes[path]))]
        if matched:
            signals[area] = matched
    return signals
