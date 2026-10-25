`acmax24` is a python library for managing the AVPro Edge AC-MAX-24 Audio Matrix.  This is a 24 input, 24 output audio matrix device, designed for whole home audio.

This library was created to enable the the `hass-acmax24` Home Assistant integration, which makes the AC-MAX-24 Audio Matrix appear as a set of MediaPlayer entities in Home Assistant. 


## Releasing

Releases are published to [PyPI](https://pypi.org/project/acmax24/) by the `Publish to PyPI` GitHub Actions workflow (`.github/workflows/publish.yml`), using PyPI trusted publishing. No API token is needed.

To cut a release:

```bash
poetry version patch          # or minor / major
git commit -am "Release v$(poetry version --short)"
git tag "v$(poetry version --short)"
git push && git push --tags
```

Pushing a `v*` tag runs the tests, builds the package and uploads it to PyPI. The workflow fails if the tag doesn't match the version in `pyproject.toml`.
