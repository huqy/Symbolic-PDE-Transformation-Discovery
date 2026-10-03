# Release provenance and authentication

Source snapshot: b6f832c66260d19744ff562a4cf1f42871cc9bb9.
This identifier documents the accepted internal release candidate. Its objects and
ancestry are not required or published. Public Git begins with a new root commit.

`reproduce/RELEASE_SOURCE_MANIFEST.json` protects exact paths, sizes, modes, SHA-256,
and roles. `reproduce/RELEASE_PROVENANCE.json` binds the complete protected
scientific set with a canonical semantic digest. Verification uses only local
release bytes, not old Git blobs or old commit ancestry. Scientific/config/protocol
and stage launcher files were compared byte-for-byte against the accepted source.
The public commit/tag protects the manifest itself. Source snapshots use a positive
manifest allowlist, excluding `reference_results/` and `public_assets/` archives.

A publication-only package adapter replaces `s0_launcher.check_source`. The public
shell interpreter shim redirects S0 module startup to `release_entry`; S1-S3 import
the same release-local check. Direct obsolete S0 module execution fails closed.
This change authenticates current accepted bytes and does not replace membership,
protocol gates, fresh receipts, input capabilities, or scientific outcomes.

Five historical absolute path strings remain in three frozen scientific protocol
JSON files to preserve their exact commitment-bearing bytes. They are inert
historical locator fields. The accepted adapters replace K1/K3 provenance and asset
opening paths with explicit fresh execution roots and staged archives. No historical
path is dereferenced by the public entry. The retained scientific protocol context
document is a frozen scientific lock, not a private context repository export.
Input manifest locator metadata now uses repository-relative public asset paths;
all commitment hashes, field IDs, row counts, roles, and capabilities are unchanged.

The qualified numerical environment is the supplied explicit conda-forge build.
Historical SciPy/BLAS exact build recovery remains incomplete; the accepted fresh
campaign is the validation authority for this qualified environment. Public packaging
does not claim a new heavy integrated execution or new untouched holdout evidence.
