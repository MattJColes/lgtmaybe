## Why

lgtmaybe is advertised for Windows but is not tested there and still fails or
silently degrades under Windows locale, path, environment, and filesystem
semantics. Releases also lack a Windows-native executable and package-manager
installation path.

## What Changes

- Add focused Windows 3.11 and 3.13 legs to the main CI matrix while retaining
  the existing Ubuntu coverage and stable fan-in check.
- Make file and subprocess text handling explicitly UTF-8, configure CLI stdio
  safely, and enforce the rule with tests and encoding warnings.
- Normalise repository paths to forward slashes and keep git path matching
  case-sensitive on every host.
- Preserve the static-analysis sandbox on Windows, discover gcloud ADC in the
  Windows config location, and reliably remove read-only temporary trees.
- Build and smoke-test a portable Windows executable for each release, attach
  it to the GitHub release, and submit subsequent versions to winget as
  `MattJColes.lgtmaybe`.
- Document Windows installation, release automation, bundled tooling, and the
  one-time winget publisher setup.

## Capabilities

### New Capabilities

- `windows-distribution`: Windows CI coverage, the portable executable release
  artifact, winget publishing, and their release gates.

### Modified Capabilities

- `cli-and-local`: CLI, config, git, and subprocess text boundaries become
  deterministic UTF-8 and CLI output remains safe under legacy Windows
  redirected-stdio encodings.
- `review-pipeline`: Static-analysis paths and sandbox environment become
  Windows-safe, and repository path filters remain case-sensitive on every
  host.
- `provider-gateway`: Ambient Vertex credentials are discovered from the
  platform-appropriate gcloud configuration directory.

## Impact

The change affects the main CI workflow, CLI/config/local text boundaries,
static-analysis and ast-grep adapters, git path filtering, provider credential
probing, temporary checkout cleanup, packaging metadata, release workflows,
release and installation documentation, and the corresponding living specs.
PyInstaller is added only to the packaging dependency group; cloud-provider
extras remain exclusive to the pip distribution.
