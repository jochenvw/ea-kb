# Typed application settings

`Settings` in `__init__.py` validates runtime limits, providers, paths and secret-bearing fields.

- Precedence: explicit values, `EKB_` environment, `.env`, selected YAML, file secrets, defaults.
- `EKB_CONFIG_FILE` selects the YAML profile; root `config` holds profile files.
- Add a setting only for a concrete requirement and keep examples/docs consistent.

Do not hardcode real credentials or weaken validation to accept invalid configuration.
See `.env.example` and profile-precedence tests in `tests\unit\test_adapters.py`.
