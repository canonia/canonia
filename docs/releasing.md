# Releasing to PyPI

Canonia publishes to [PyPI](https://pypi.org/project/canonia/) via **Trusted
Publishing** (OIDC) — GitHub Actions uploads on your behalf, so there are no API
tokens or secrets in the repo. The workflow is [`.github/workflows/publish.yml`],
triggered when you publish a GitHub Release.

## One-time setup (project owner)

Configure a *pending publisher* on PyPI so it trusts this repo's workflow. On
[pypi.org](https://pypi.org) → the `canonia` project → **Publishing** → **Add a
pending publisher** (or do it before the first upload — that's what "pending"
means):

| Field         | Value          |
| ------------- | -------------- |
| PyPI Project  | `canonia`      |
| Owner         | `canonia`      |
| Repository    | `canonia`      |
| Workflow name | `publish.yml`  |
| Environment   | `pypi`         |

That's it — no token to generate or store. (Optionally create a matching GitHub
Environment named `pypi` under repo Settings → Environments to gate releases
behind a required reviewer.)

## Cutting a release

1. **Bump the version** in two places (they must agree — the workflow enforces
   the tag matches too):
   - `pyproject.toml` → `project.version`
   - `src/canonia/__init__.py` → `__version__`
2. Commit on `main` (CI must be green).
3. **Tag and publish a GitHub Release.** The tag must be the version, optionally
   `v`-prefixed — the workflow strips a leading `v` and fails the build if it
   doesn't match `pyproject.toml`:
   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   gh release create v0.1.0 --title "v0.1.0" --notes "…"
   ```
4. The `publish` workflow builds the sdist + wheel, runs `twine check`, and
   uploads to PyPI over OIDC. Watch it under the repo's **Actions** tab.
5. Verify: `pip install canonia==0.1.0` in a clean venv.

## Rehearse on TestPyPI (optional)

To dry-run the whole path first, add a second pending publisher on
[test.pypi.org](https://test.pypi.org) with the same fields, then upload a build
manually:

```bash
python -m build
twine upload --repository testpypi dist/*      # uses your TestPyPI token
pip install --index-url https://test.pypi.org/simple/ canonia
```

## After the first real release

Pin canon's validation workflow off the moving `git+…@main` install to the
published version (framework open point #9):

```yaml
# canon/.github/workflows/validate.yml
- run: python -m pip install 'canonia==0.1.0'
```

[`.github/workflows/publish.yml`]: ../.github/workflows/publish.yml
