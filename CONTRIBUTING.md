# Contributing

Open an issue before a large PR, so we can agree on the approach first. Small fixes can go straight to a PR.

## Setup

```sh
git clone https://github.com/spencer2211/heelr.git
cd heelr
ln -s "$PWD/bin/heelr" ~/.local/bin/heelr   # or any directory on your PATH
```

## Tests

- `python3 -m pytest` runs the offline suite.
- `heelr selftest` runs against a throwaway Herdr server and never touches yours.
- Maintainers can add private patterns to a git-ignored `.privacy-deny` at the repo root (one regex per line, `#` comments allowed); `tests/test_privacy.py` reads it when present.

## Flow

Branch, then PR, then CI (`tests` on Python 3.11 and 3.13), then code-owner review.

## Changelog

Add an entry under "Unreleased" in `CHANGELOG.md`. Versions follow semver:

> Versions follow [semver](https://semver.org). Before 1.0, a breaking config or CLI change bumps the
> minor version; features and fixes bump the patch. Tags are `vX.Y.Z`.

## Security

Report vulnerabilities as described in [SECURITY.md](SECURITY.md), not in a public issue.
