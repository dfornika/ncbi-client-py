# Roadmap

## Where things stand

- **Datasets v2**: report/metadata endpoints (taxonomy, genome, gene, biosample, virus), plus genome and gene package downloads (`client.download_genome(...)`, `client.download_gene(...)`) that stream a zip (FASTA/GFF/annotation/data catalog) to disk. ✅ Done.
- **E-utilities**: `einfo`, `esearch`, `esummary`, `elink`, `elink_available`, `efetch`, `epost`. History-server (`WebEnv`/`query_key`) support is threaded through `esearch`/`esummary`/`efetch`/`elink` for batches too large for a URL. ✅ Done.
- **Bridge**: connects `esearch`/`esummary` results to Datasets entities for `gene`, `taxonomy`, `assembly`, `biosample`.
- **BLAST**: submit/poll/fetch wrapped behind a blocking `client.blast()`, plus an async prototype (`AsyncNCBIClient`) scoped to BLAST only.
- **Nothing SRA-related exists yet.** No download support, no submission support.

## Proposed phases

### Phase 1 — foundational, low-risk, pure API wrapping

**Flesh out E-utilities** ✅ Done
- `efetch`: retrieve full records (FASTA, GenBank flat file, XML) by UID, or by a history-server handle.
- `epost` + history server (`WebEnv`/`query_key`) support threaded through `esearch`/`esummary`/`efetch`/`elink`, for ID lists too large to pass as a URL parameter.
- Lower priority, not yet done: `espell`, `egquery`, `ecitmatch` — add only if something concrete needs them.

**Datasets "download" endpoints** ✅ Done
- `datasets.download(client, operation, params, destination)` plus `client.download_genome(...)`/`client.download_gene(...)` stream a Datasets v2 zip package (`/genome/accession/{accessions}/download`, `/gene/id/{gene_ids}/download`) directly to disk, separate from the JSON `_report` code path.
- Atomic write (temp file + rename) so a failed/interrupted download never leaves a corrupt file at the destination. Known limitation: a connection drop mid-stream isn't retried (only pre-body 429/5xx are); resuming safely would need `Range` request support, which isn't implemented.
- This unblocks "BioSample associated assembly files" below, since assembly FASTA/GFF is now reachable via the genome download endpoint.

### Phase 2 — builds on Phase 1 primitives

**BioSample metadata + associated files**
- Metadata: already partly there via `client.biosample()`; worth revisiting once BioProject linkage (see "other ideas" below) is clearer.
- Associated assembly files: BioSample → linked assembly accession (via `elink` or the existing `genome-dataset-reports-by-biosample-id` operation) → Datasets download endpoint (Phase 1).
- Associated FASTQ files: BioSample → linked SRA run accessions (via `elink`, `biosample` → `sra`) → SRA FASTQ/`.sra` download (below).

**SRA FASTQ / `.sra` download**
- Design decision (resolved): default to NCBI's own delivery mechanism, returning the raw `.sra` file (or a signed cloud URL to it) and leaving FASTQ conversion (`fasterq-dump` or equivalent) to the caller — keeps the default path NCBI-only and doesn't force a conversion cost on every call.
- Add the ENA Portal API (`https://www.ebi.ac.uk/ena/portal/api/filereport?...&fields=fastq_ftp`) as an opt-in convenience path that returns direct `fastq.gz` URLs over plain HTTPS — no local conversion needed, but depends on an EBI/ENA service rather than NCBI's own infrastructure. Document this dependency clearly wherever it's used.
- Needs research before implementation: confirm NCBI's current officially-supported way to fetch `.sra` bytes/signed URLs by run accession (the "SRA Data Locator" / cloud delivery service) since this isn't a stable, well-documented REST endpoint the way Datasets/eutils are.

### Phase 3 — higher complexity, needs care

**Create BioSample + upload reads to SRA**
- There's no modern public REST API for this. NCBI's process is: email `sra@ncbi.nlm.nih.gov` to request a "center account" for programmatic XML submission, generate Submission/BioSample/SRA XML descriptors, transfer the XML + data files via FTP/Aspera/S3, then poll a report file NCBI generates for processing status.
- This creates real, public, hard-to-reverse records (a live BioSample/SRA accession) once submitted for real. Any implementation should:
  - Start as a research spike (confirm current XML schema, confirm NCBI's test/sandbox submission area) before writing library code.
  - Only be tested against NCBI's sandbox area, never production, until the user explicitly wants a real submission.
  - Require explicit confirmation for any call that would submit to the production endpoint — this shouldn't be a "default yes" operation given the consequences of a mistake.
- Treat this as the last item to tackle, once the read-side (Phases 1–2) is solid.

## Other ideas worth considering

- **BioProject support**: the parent container linking BioSamples, SRA runs, and assemblies together. Datasets v2 doesn't appear to have a first-class BioProject report endpoint (unconfirmed — worth checking directly); may need to lean on eutils' `bioproject` database instead.
- **Streaming/resumable downloads**: assembly packages and FASTQ files can be gigabytes. The current client design assumes small JSON responses; large-file downloads need streaming-to-disk and probably resumable `Range` requests, which is a different code path from everything else in the client.
- **Async parity beyond BLAST**: now that the sync/async pattern is proven, extending it to eutils/Datasets would help once there's bulk/batch usage (e.g., looping over hundreds of BioSamples) — but hold off until there's an actual concurrent workload driving it, per the same reasoning that kept `AsyncNCBIClient` BLAST-only so far.
- **A thin CLI**: `ncbi-client fetch-sra <accession>`, `ncbi-client blast query.fasta`, etc. Worth doing once enough of the file-download story exists to make a CLI useful; low priority until then.
- **Caching for read-heavy metadata** (taxonomy, gene lookups that don't change often): a "nice to have," deferred indefinitely unless something concrete needs it.

## Suggested order

1. ~~`efetch` + history server support (eutils)~~ ✅ Done
2. ~~Datasets download endpoints (genome/gene packages)~~ ✅ Done
3. SRA `.sra`/FASTQ download (NCBI-default, ENA-convenience)
4. BioSample metadata + associated-file glue (assemblies, FASTQs)
5. BioProject support, if it turns out to matter for the above
6. BioSample creation + SRA read upload (research spike first, sandbox-only)
7. Nice-to-haves: streaming/resumable downloads, broader async, CLI, caching
