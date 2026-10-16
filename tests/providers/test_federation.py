"""A CI run's own OIDC ID token becomes keyless cloud credentials in the container.

On GitHub the composite action does this exchange in its own steps. A Forgejo
Actions job runs the lgtmaybe image directly, so the entrypoint fetches the
workflow's ID token itself and hands it to each cloud SDK through the variables
that SDK already reads. Credential resolution itself does not change.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from lgtmaybe.providers.federation import FederationError, federate_ci_credentials

REQUEST_URL = (
    "https://forgejo.example.com/api/actions/_apis/pipelines/workflows/7/idtoken?api-version=2.0"
)
REQUEST_TOKEN = "request-bearer-secret"
ID_TOKEN = "eyJhbGciOiJSUzI1NiJ9.payload.signature"
RUNNER_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": REQUEST_URL,
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_TOKEN,
}
GCP_PROVIDER = "projects/123/locations/global/workloadIdentityPools/pool/providers/forgejo"


class TokenEndpoint:
    """A stub of the runner's ID-token endpoint that records each request."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = {"value": ID_TOKEN} if body is None else body
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)

        return httpx.Client(transport=httpx.MockTransport(handle))

    @property
    def audiences(self) -> list[str | None]:
        return [request.url.params.get("audience") for request in self.requests]


def _federate(env: dict[str, str], endpoint: TokenEndpoint, tmp_path: Path) -> dict[str, str]:
    return federate_ci_credentials(env, client=endpoint.client(), workdir=tmp_path)


class TestGate:
    def test_no_input_requests_nothing_and_changes_nothing(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()

        assert _federate(dict(RUNNER_ENV), endpoint, tmp_path) == {}
        assert endpoint.requests == []
        assert list(tmp_path.iterdir()) == []

    def test_blank_inputs_count_as_unset(self, tmp_path: Path) -> None:
        """An unset optional workflow input arrives as an empty string."""
        endpoint = TokenEndpoint()
        env = {**RUNNER_ENV, "INPUT_AWS_ROLE_ARN": "", "INPUT_AZURE_CLIENT_ID": " "}

        assert _federate(env, endpoint, tmp_path) == {}
        assert endpoint.requests == []


class TestFailures:
    @pytest.mark.parametrize("missing", sorted(RUNNER_ENV))
    def test_a_missing_id_token_names_the_workflow_setting(
        self, missing: str, tmp_path: Path
    ) -> None:
        endpoint = TokenEndpoint()
        env = {
            **RUNNER_ENV,
            "INPUT_AWS_ROLE_ARN": "arn:aws:iam::1:role/r",
            "AWS_REGION": "us-east-1",
        }
        del env[missing]

        with pytest.raises(FederationError) as excinfo:
            _federate(env, endpoint, tmp_path)

        message = str(excinfo.value)
        assert "enable-openid-connect: true" in message
        assert "id-token: write" in message
        assert endpoint.requests == []

    def test_azure_without_a_tenant_fails_before_any_request(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()
        env = {**RUNNER_ENV, "INPUT_AZURE_CLIENT_ID": "client"}

        with pytest.raises(FederationError, match="INPUT_AZURE_TENANT_ID"):
            _federate(env, endpoint, tmp_path)
        assert endpoint.requests == []

    def test_bedrock_without_a_region_fails_before_any_request(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()
        env = {**RUNNER_ENV, "INPUT_AWS_ROLE_ARN": "arn:aws:iam::1:role/r"}

        with pytest.raises(FederationError, match="INPUT_AWS_REGION"):
            _federate(env, endpoint, tmp_path)
        assert endpoint.requests == []

    def test_a_refused_request_names_the_status_and_leaks_nothing(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint(status=403, body={"message": f"bad token {REQUEST_TOKEN}"})
        env = {
            **RUNNER_ENV,
            "INPUT_AWS_ROLE_ARN": "arn:aws:iam::1:role/r",
            "AWS_REGION": "eu-west-1",
        }

        with pytest.raises(FederationError) as excinfo:
            _federate(env, endpoint, tmp_path)

        message = str(excinfo.value)
        assert "403" in message
        assert REQUEST_TOKEN not in message
        assert "api-version" not in message
        assert "idtoken" not in message

    def test_a_response_without_a_token_fails(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint(body={"count": 0})
        env = {**RUNNER_ENV, "INPUT_AZURE_CLIENT_ID": "c", "INPUT_AZURE_TENANT_ID": "t"}

        with pytest.raises(FederationError, match="no token"):
            _federate(env, endpoint, tmp_path)


class TestBedrock:
    ENV = {
        **RUNNER_ENV,
        "INPUT_AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/lgtmaybe",
        "INPUT_AWS_REGION": "us-west-2",
    }

    def test_requests_the_sts_audience_with_the_runner_bearer(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()

        _federate(dict(self.ENV), endpoint, tmp_path)

        (request,) = endpoint.requests
        assert endpoint.audiences == ["sts.amazonaws.com"]
        assert request.url.params.get("api-version") == "2.0", "the runner's own query survives"
        assert request.headers["Authorization"] == f"Bearer {REQUEST_TOKEN}"

    def test_sets_the_variables_botocore_reads(self, tmp_path: Path) -> None:
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)

        assert updates["AWS_ROLE_ARN"] == "arn:aws:iam::123456789012:role/lgtmaybe"
        assert updates["AWS_REGION"] == "us-west-2"
        assert updates["AWS_ROLE_SESSION_NAME"] == "lgtmaybe"
        token_file = Path(updates["AWS_WEB_IDENTITY_TOKEN_FILE"])
        assert token_file.read_text(encoding="utf-8") == ID_TOKEN

    def test_an_ambient_region_satisfies_the_input_set(self, tmp_path: Path) -> None:
        env = {
            **RUNNER_ENV,
            "INPUT_AWS_ROLE_ARN": "arn:aws:iam::1:role/r",
            "AWS_REGION": "ap-southeast-2",
        }

        updates = _federate(env, TokenEndpoint(), tmp_path)

        assert "AWS_REGION" not in updates, "an ambient region is left as it is"

    def test_botocore_resolves_a_web_identity_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        botocore_session = pytest.importorskip("botocore.session")
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in updates.items():
            monkeypatch.setenv(name, value)

        resolver = botocore_session.Session().get_component("credential_provider")
        provider = resolver.get_provider("assume-role-with-web-identity")

        credentials = provider.load()  # deferred: no STS call until a key is read
        assert credentials is not None
        assert credentials.method == "assume-role-with-web-identity"


class TestAzure:
    ENV = {**RUNNER_ENV, "INPUT_AZURE_CLIENT_ID": "client-id", "INPUT_AZURE_TENANT_ID": "tenant-id"}

    def test_requests_the_entra_audience(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()

        _federate(dict(self.ENV), endpoint, tmp_path)

        assert endpoint.audiences == ["api://AzureADTokenExchange"]

    def test_sets_the_variables_workload_identity_reads(self, tmp_path: Path) -> None:
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)

        assert updates["AZURE_CLIENT_ID"] == "client-id"
        assert updates["AZURE_TENANT_ID"] == "tenant-id"
        token_file = Path(updates["AZURE_FEDERATED_TOKEN_FILE"])
        assert token_file.read_text(encoding="utf-8") == ID_TOKEN

    def test_default_azure_credential_picks_workload_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        identity = pytest.importorskip("azure.identity")
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)
        for name, value in updates.items():
            monkeypatch.setenv(name, value)

        credential = identity.DefaultAzureCredential()

        kinds = {type(c).__name__ for c in credential.credentials}
        assert "WorkloadIdentityCredential" in kinds


class TestVertex:
    ENV = {**RUNNER_ENV, "INPUT_GCP_WIF_PROVIDER": GCP_PROVIDER}

    def test_requests_the_provider_audience(self, tmp_path: Path) -> None:
        endpoint = TokenEndpoint()

        _federate(dict(self.ENV), endpoint, tmp_path)

        assert endpoint.audiences == [f"https://iam.googleapis.com/{GCP_PROVIDER}"]

    def test_writes_an_external_account_config(self, tmp_path: Path) -> None:
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)

        config = json.loads(
            Path(updates["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8")
        )
        assert config["type"] == "external_account"
        assert config["audience"] == f"//iam.googleapis.com/{GCP_PROVIDER}"
        assert config["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
        assert config["token_url"] == "https://sts.googleapis.com/v1/token"
        token_file = Path(config["credential_source"]["file"])
        assert token_file.read_text(encoding="utf-8") == ID_TOKEN
        assert "service_account_impersonation_url" not in config

    def test_impersonates_a_service_account_when_given(self, tmp_path: Path) -> None:
        env = {**self.ENV, "INPUT_GCP_SERVICE_ACCOUNT": "reviewer@proj.iam.gserviceaccount.com"}

        updates = _federate(env, TokenEndpoint(), tmp_path)

        config = json.loads(
            Path(updates["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8")
        )
        assert config["service_account_impersonation_url"] == (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            "reviewer@proj.iam.gserviceaccount.com:generateAccessToken"
        )

    def test_google_auth_loads_the_config(self, tmp_path: Path) -> None:
        identity_pool = pytest.importorskip("google.auth.identity_pool")
        updates = _federate(dict(self.ENV), TokenEndpoint(), tmp_path)

        credentials = identity_pool.Credentials.from_file(updates["GOOGLE_APPLICATION_CREDENTIALS"])

        assert credentials.retrieve_subject_token(None) == ID_TOKEN


class TestSecrecy:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_token_files_are_owner_only(self, tmp_path: Path) -> None:
        env = {**TestAzure.ENV, "INPUT_GCP_WIF_PROVIDER": GCP_PROVIDER}

        updates = _federate(env, TokenEndpoint(), tmp_path)

        for path in (
            updates["AZURE_FEDERATED_TOKEN_FILE"],
            updates["GOOGLE_APPLICATION_CREDENTIALS"],
        ):
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_each_run_writes_into_a_fresh_directory(self) -> None:
        """No workdir given: a private temporary directory, never a shared path."""
        endpoint = TokenEndpoint()

        updates = federate_ci_credentials(dict(TestAzure.ENV), client=endpoint.client())

        token_dir = Path(updates["AZURE_FEDERATED_TOKEN_FILE"]).parent
        assert token_dir.name.startswith("lgtmaybe-oidc-")
        if sys.platform != "win32":
            assert stat.S_IMODE(os.stat(token_dir).st_mode) == 0o700
