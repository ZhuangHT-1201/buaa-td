# Contributing

Thanks for considering a contribution to BUAA TD Automation Console.

## Development Setup

1. Install Python 3.10 or newer.
2. Clone the repository.
3. Optionally install image-format support and the package in editable mode:

```bash
python -m pip install -e ".[images]"
```

4. Run the test suite:

```bash
python -m unittest discover -v
```

5. Launch the console:

```bash
python -m buaatd gui
```

## Pull Requests

- Keep changes focused and explain the user-facing reason.
- Add or update tests for behavior changes.
- Preserve both Chinese and English interface strings.
- Do not commit `users.txt`, `data/`, logs, generated photos, process IDs, or credentials.
- Use clear commit messages and avoid adding unrelated generated files.

## Privacy and Safety

- Do not include real student IDs, names, photos, or server payloads in tests or screenshots.
- Use temporary directories and synthetic data in automated tests.
- Avoid adding telemetry, analytics, or remote uploads without explicit documentation.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
