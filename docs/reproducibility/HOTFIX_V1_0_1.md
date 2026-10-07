# v1.0.1 runtime closure and portable resume

Use v1.0.1-paper-submission for complete reproduction. The immutable
v1.0.0-paper-submission tag remains available as reference, but has a known
complete-workflow blocker at S1-to-S2 and an overly strict cross-node resume
identity. This patch repairs publication/runtime plumbing only.

The exact accepted S2 pre-response commitment metadata is now included. S3
commitment metadata is also included, with unused internal archive paths removed;
stage projections bind the explicit public staging directory as before. These
headers contain archive and public row commitments, without response outcomes,
candidate labels or historical decisions. All frozen scientific files and five
public input archives are unchanged.

The coordinator validates Linux x86_64, exact installed conda package/build records
against the public lock, Python/NumPy/SciPy/threadpoolctl versions, OpenBLAS 0.3.30
pthreads and one-thread settings on every invocation. Resume compares a canonical
fingerprint and the exact source, assets, manifest, work/staging roots and
reauthenticated receipts. OpenBLAS CPU architecture, executable/library paths and
kernel observations are retained in each coordinator invocation event and do not
bind compatible scheduler allocations. Legacy v1.0.0 roots are diagnostic only;
start v1.0.1 from a fresh work root. No root migration is provided.

Lightweight validation includes source-file dependency fault injection, actual
S1-to-S2 and S2-to-S3 handoffs and shell dry-run plans over authenticated synthetic
fixtures, metadata projections, cross-node resume acceptance and numerical,
source, asset and receipt drift refusal. Synthetic cohort sizes vary and never
serve as scientific targets. No DEVELOPMENT or SEALED payload is opened by these
tests. Reference results remain non-authoritative.

No heavy end-to-end v1.0.1 run has completed yet. External hotfix review and a
separately authorized manual clean v1.0.1 run are the next validation steps.
