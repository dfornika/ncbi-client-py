# Roadmap

## Where things stand

- **Datasets v2**: report/metadata endpoints (taxonomy, genome, gene, biosample, virus), plus genome and gene package downloads (`client.download_genome(...)`, `client.download_gene(...)`) that stream a zip (FASTA/GFF/annotation/data catalog) to disk. ✅ Done.
- **E-utilities**: `einfo`, `esearch`, `esummary`, `elink`, `elink_available`, `efetch`, `epost`. History-server (`WebEnv`/`query_key`) support is threaded through `esearch`/`esummary`/`efetch`/`elink` for batches too large for a URL. ✅ Done.
- **Bridge**: connects `esearch`/`esummary` results to Datasets entities for `gene`, `taxonomy`, `assembly`, `biosample`.
- **BLAST**: submit/poll/fetch wrapped behind a blocking `client.blast()`, plus an async prototype (`AsyncNCBIClient`) scoped to BLAST only.
- **SRA**: `client.download_sra(...)` (public S3 Open Data bucket first, SDL resolver fallback), `client.copy_sra_to_s3(...)` (direct S3-to-S3 server-side copy via boto3, optional `[s3]` extra), `client.download_fastq(...)` (ENA Portal API convenience path). No submission support.
- **BioSample glue**: `client.biosample_assembly_accessions(...)` / `client.biosample_sra_run_accessions(...)` resolve a BioSample accession to its linked assembly and SRA run accessions; `client.download_biosample_assemblies(...)` / `client.download_biosample_fastqs(...)` chain those into the existing download primitives. ✅ Done.
- **BioSample creation** (`client.submit_biosamples(...)` / `poll_biosample_submission(...)` / `submit_biosamples_and_wait(...)`, `src/ncbi_client/submission.py`): builds NCBI's UI-less Submission Protocol XML, uploads via SFTP (paramiko, optional `[sftp]` extra), polls for the resulting `report.<N>.xml`. **Validated only against a local Docker `atmoz/sftp` server + a hand-rolled fake-portal test script — never against real NCBI infrastructure (test or production).** SRA read submission is not implemented (BioSample-only for this cut).

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

**BioSample metadata + associated files** ✅ Done
- `bridge.biosample_assembly_accessions`: BioSample accession → linked assembly accession(s), via the existing `genome-dataset-reports-by-biosample-id` Datasets operation (it accepts a BioSample accession directly as the path param, no elink/UID hop needed).
- `bridge.biosample_sra_run_accessions`: BioSample accession → linked SRA run accession(s) (`SRR.../ERR.../DRR...`). Needs an `esearch` hop first (BioSample's elink UID isn't its accession), then `elink` (`biosample_sra`), then `esummary(db="sra")` — whose DocSum embeds run accessions as an XML fragment (`<Run acc="...">`) inside a JSON string field rather than structured JSON, so this parses that out with `ElementTree`.
- `client.download_biosample_assemblies(...)`/`client.download_biosample_fastqs(...)` chain the resolvers into the existing `datasets.download`/`sra.download_fastq` primitives.
- Bonus fix along the way: `bridge.DB_TO_DATASETS["biosample"]["id_from"]` was `"biosampleaccn"`, but a BioSample's own `esummary` carries its accession under `"accession"` (`"biosampleaccn"` is a cross-reference field that appears on *other* dbs' summaries, e.g. assembly, pointing back to BioSample) — this silently broke `lookup_dataset_entity(client, "biosample", ...)` for any real BioSample summary; fixed and covered by a regression test.
- Metadata beyond this (deeper BioProject-aware linkage) still worth revisiting once BioProject support (below) exists.

**SRA FASTQ / `.sra` download** ✅ Done
- `download_sra`: tries NCBI's public S3 Open Data bucket (`sra-pub-run-odp`, plain HTTPS GET, object key `sra/{accession}/{accession}`) first; falls back to the SDL resolver API (`locate.ncbi.nlm.nih.gov/sdl/2/retrieve`) on a 403/404 (not every run is mirrored to the ODP bucket — dbGaP-protected, cold-storage, or lag-affected runs can be missing). Leaves FASTQ conversion (`fasterq-dump` or equivalent) to the caller.
- `copy_sra_to_s3`: direct server-side S3-to-S3 copy of the raw `.sra` object into a caller-owned bucket via boto3's high-level `copy()` (handles multipart for >5GB files), without routing bytes through the local machine. boto3 is an optional extra (`ncbi-client[s3]`), not a required dependency. AWS S3 destinations only (SDL can resolve GCS locations too, but that's out of scope).
- `download_fastq`: opt-in convenience path via the ENA Portal API (`https://www.ebi.ac.uk/ena/portal/api/filereport?...&fields=fastq_ftp`), returning actual downloaded `fastq.gz` file(s) — no local conversion needed, but depends on EBI/ENA rather than NCBI. Raises a clear error if ENA has no auto-converted FASTQ for a run (10x/cellranger, PacBio/Nanopore native, Complete Genomics native submissions).
- BioSample → SRA-run-accession resolution is now done too (see the item above).

### Phase 3 — higher complexity, needs care

**Create BioSample** ✅ Done (BioSample-only; SRA read upload still open — see below)
- No modern public REST API for this — implemented against NCBI's 2016-vintage "UI-less Data Submission Protocol": build a `submission.xml` (`<Submission><Description>...<Action><AddData target_db="BioSample">...`), upload it + a `submit.ready` sentinel via SFTP (paramiko, `ncbi-client[sftp]` extra, defaults to `RejectPolicy` for host keys — stricter than the reference pipeline this was researched from), then poll for NCBI's `report.<N>.xml` (per-action status: `Queued`/`Processing`/`Processed-ok`/`Processed-error`/`Deleted`, submission status derived via the documented precedence rule).
- `host`/`remote_base_path` have no defaults anywhere in `submission.py` — there is no way to accidentally reach a real server, test or production, without deliberately supplying its address.
- **Validation is local-only for this cut, by explicit design**: a Docker `atmoz/sftp` container plus a hand-rolled `tests/manual/fake_submission_portal.py` (happy-path-only stand-in for NCBI's report-generation, since no such simulator exists anywhere in the wild — confirmed via research) were used to prove the full submit→poll→accession round-trip works. This proves the library's SFTP/XML/polling mechanics are correct; it does **not** prove NCBI's real Submission Portal will accept this XML (real schema/package/attribute validation, real timing) — that needs an eventual real NCBI test-path (`submit/Test`) submission, which is a deliberate, separate future step requiring explicit sign-off, not something this cut did.
- **SRA read upload** (`AddFiles target_db="SRA"` actions) is a separate, not-yet-built feature — `submission.py` is factored so its transport/polling plumbing (SFTP connect/upload/poll/report-parsing) is reusable for it without a rewrite, only a new XML-builder function and top-level `submit_sra(...)`-style entry point would be needed.
- BioProject creation is out of scope — BioSample submissions reference an already-existing BioProject via `PrimaryId`, matching how `bridge.biosample_assembly_accessions`/`biosample_sra_run_accessions` already assume pre-existing accessions elsewhere in this library.

## Other ideas worth considering

- **BioProject support**: the parent container linking BioSamples, SRA runs, and assemblies together. Datasets v2 doesn't appear to have a first-class BioProject report endpoint (unconfirmed — worth checking directly); may need to lean on eutils' `bioproject` database instead.
- **Streaming/resumable downloads**: assembly packages and FASTQ files can be gigabytes. The current client design assumes small JSON responses; large-file downloads need streaming-to-disk and probably resumable `Range` requests, which is a different code path from everything else in the client.
- **Async parity beyond BLAST**: now that the sync/async pattern is proven, extending it to eutils/Datasets would help once there's bulk/batch usage (e.g., looping over hundreds of BioSamples) — but hold off until there's an actual concurrent workload driving it, per the same reasoning that kept `AsyncNCBIClient` BLAST-only so far.
- **A thin CLI**: `ncbi-client fetch-sra <accession>`, `ncbi-client blast query.fasta`, etc. Worth doing once enough of the file-download story exists to make a CLI useful; low priority until then.
- **Caching for read-heavy metadata** (taxonomy, gene lookups that don't change often): a "nice to have," deferred indefinitely unless something concrete needs it.

## Suggested order

1. ~~`efetch` + history server support (eutils)~~ ✅ Done
2. ~~Datasets download endpoints (genome/gene packages)~~ ✅ Done
3. ~~SRA `.sra`/FASTQ download (NCBI-default, ENA-convenience)~~ ✅ Done
4. ~~BioSample metadata + associated-file glue (assemblies, FASTQs)~~ ✅ Done
5. BioProject support, if it turns out to matter for the above
6. ~~BioSample creation~~ ✅ Done, local-only (see above) — SRA read upload via the same submission plumbing is still open
7. Real NCBI test-path (`submit/Test`) validation of BioSample creation, once explicitly requested
8. Nice-to-haves: streaming/resumable downloads, broader async, CLI, caching
