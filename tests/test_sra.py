from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response

from ncbi_client import sra

ODP_URL = f"{sra.SRA_ODP_BASE_URL}/sra/SRR390728/SRR390728"


def _sdl_response(locations):
    return {
        "version": "2",
        "result": [
            {
                "bundle": "SRR390728",
                "status": 200,
                "files": [{"type": "sra", "locations": locations}],
            }
        ],
    }


# --- .sra download ---


def test_download_sra_from_odp_success(client, tmp_path):
    sra_bytes = b"NCBI.sra fake bytes"
    with respx.mock:
        respx.get(ODP_URL).mock(return_value=Response(200, content=sra_bytes))
        destination = tmp_path / "SRR390728.sra"
        result = sra.download_sra(client, "SRR390728", destination)

    assert result == destination
    assert destination.read_bytes() == sra_bytes
    assert [p for p in tmp_path.iterdir() if p.suffix == ".part"] == []


@pytest.mark.parametrize("odp_status", [403, 404])
def test_download_sra_odp_miss_falls_back_to_sdl(client, tmp_path, odp_status):
    sra_bytes = b"fallback .sra bytes"
    resolved_link = "https://sra-download.ncbi.nlm.nih.gov/traces/sra/SRR390728"
    with respx.mock:
        respx.get(ODP_URL).mock(return_value=Response(odp_status, content=b"not found"))
        respx.get(sra.SDL_BASE_URL).mock(
            return_value=Response(
                200,
                json=_sdl_response([{"service": "sra-ncbi", "link": resolved_link, "payRequired": False}]),
            )
        )
        respx.get(resolved_link).mock(return_value=Response(200, content=sra_bytes))

        destination = tmp_path / "SRR390728.sra"
        result = sra.download_sra(client, "SRR390728", destination)

    assert result.read_bytes() == sra_bytes


def test_download_sra_not_found_anywhere_raises(client, tmp_path):
    with respx.mock:
        respx.get(ODP_URL).mock(return_value=Response(404, content=b"not found"))
        respx.get(sra.SDL_BASE_URL).mock(return_value=Response(200, json=_sdl_response([])))

        destination = tmp_path / "SRR390728.sra"
        with pytest.raises(sra.SRAError):
            sra.download_sra(client, "SRR390728", destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_sra_retries_on_5xx(client, tmp_path):
    sra_bytes = b"retried .sra bytes"
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(503)
        return Response(200, content=sra_bytes)

    with respx.mock:
        respx.get(ODP_URL).mock(side_effect=side_effect)
        destination = tmp_path / "SRR390728.sra"
        result = sra.download_sra(client, "SRR390728", destination)

    assert call_count == 2
    assert result.read_bytes() == sra_bytes


def test_sdl_pick_location_prefers_sra_ncbi_over_s3():
    response = _sdl_response(
        [
            {"service": "s3", "link": "https://example.com/s3-link", "payRequired": False},
            {"service": "sra-ncbi", "link": "https://example.com/ncbi-link", "payRequired": False},
        ]
    )
    location = sra._sdl_pick_location(response, "SRR390728")
    assert location["service"] == "sra-ncbi"


def test_sdl_pick_location_skips_gcs_and_pay_required():
    response = _sdl_response(
        [
            {"service": "gs", "link": "https://example.com/gs-link", "payRequired": False},
            {"service": "s3", "link": "https://example.com/pay-link", "payRequired": True},
            {"service": "s3", "link": "https://example.com/free-s3-link", "payRequired": False},
        ]
    )
    location = sra._sdl_pick_location(response, "SRR390728")
    assert location["link"] == "https://example.com/free-s3-link"


def test_sdl_pick_location_raises_when_nothing_usable():
    with pytest.raises(sra.SRAError):
        sra._sdl_pick_location(_sdl_response([]), "SRR390728")


# --- FASTQ via ENA ---


def _ena_record(fastq_ftp):
    return [{"fastq_ftp": fastq_ftp, "fastq_md5": "", "fastq_bytes": ""}]


def test_download_fastq_paired_end(client, tmp_path):
    r1 = "ftp.sra.ebi.ac.uk/vol1/fastq/SRR390/SRR390728/SRR390728_1.fastq.gz"
    r2 = "ftp.sra.ebi.ac.uk/vol1/fastq/SRR390/SRR390728/SRR390728_2.fastq.gz"
    with respx.mock:
        respx.get(sra.ENA_FILEREPORT_URL).mock(return_value=Response(200, json=_ena_record(f"{r1};{r2}")))
        respx.get(f"https://{r1}").mock(return_value=Response(200, content=b"read1"))
        respx.get(f"https://{r2}").mock(return_value=Response(200, content=b"read2"))

        result = sra.download_fastq(client, "SRR390728", tmp_path)

    assert [p.name for p in result] == ["SRR390728_1.fastq.gz", "SRR390728_2.fastq.gz"]
    assert result[0].read_bytes() == b"read1"
    assert result[1].read_bytes() == b"read2"


def test_download_fastq_single_end(client, tmp_path):
    r1 = "ftp.sra.ebi.ac.uk/vol1/fastq/SRR390/SRR390728/SRR390728.fastq.gz"
    with respx.mock:
        respx.get(sra.ENA_FILEREPORT_URL).mock(return_value=Response(200, json=_ena_record(r1)))
        respx.get(f"https://{r1}").mock(return_value=Response(200, content=b"read"))

        result = sra.download_fastq(client, "SRR390728", tmp_path)

    assert len(result) == 1
    assert result[0].read_bytes() == b"read"


def test_download_fastq_empty_fastq_ftp_raises(client, tmp_path):
    with respx.mock:
        respx.get(sra.ENA_FILEREPORT_URL).mock(return_value=Response(200, json=_ena_record("")))
        with pytest.raises(sra.SRAError, match="fastq_ftp is empty"):
            sra.download_fastq(client, "SRR390728", tmp_path)


def test_download_fastq_no_results_raises(client, tmp_path):
    with respx.mock:
        respx.get(sra.ENA_FILEREPORT_URL).mock(return_value=Response(200, json=[]))
        with pytest.raises(sra.SRAError, match="no results"):
            sra.download_fastq(client, "SRR390728", tmp_path)


def test_download_fastq_uses_https_scheme(client, tmp_path):
    r1 = "ftp.sra.ebi.ac.uk/vol1/fastq/SRR390/SRR390728/SRR390728.fastq.gz"
    with respx.mock:
        respx.get(sra.ENA_FILEREPORT_URL).mock(return_value=Response(200, json=_ena_record(r1)))
        route = respx.get(f"https://{r1}").mock(return_value=Response(200, content=b"read"))

        sra.download_fastq(client, "SRR390728", tmp_path)

    assert route.calls[0].request.url.scheme == "https"


# --- S3-to-S3 copy ---


def test_copy_sra_to_s3_calls_client_copy_with_expected_args():
    s3_client = MagicMock()
    result = sra.copy_sra_to_s3("SRR390728", "my-bucket", s3_client=s3_client)

    s3_client.copy.assert_called_once_with(
        CopySource={"Bucket": "sra-pub-run-odp", "Key": "sra/SRR390728/SRR390728"},
        Bucket="my-bucket",
        Key="SRR390728",
    )
    assert result == "s3://my-bucket/SRR390728"


def test_copy_sra_to_s3_custom_dest_key():
    s3_client = MagicMock()
    result = sra.copy_sra_to_s3("SRR390728", "my-bucket", "runs/SRR390728.sra", s3_client=s3_client)

    assert s3_client.copy.call_args.kwargs["Key"] == "runs/SRR390728.sra"
    assert result == "s3://my-bucket/runs/SRR390728.sra"


def test_copy_sra_to_s3_raises_actionable_error_without_boto3(monkeypatch):
    monkeypatch.setattr(sra, "boto3", None)
    with pytest.raises(sra.SRAError, match=r"pip install ncbi-client\[s3\]"):
        sra.copy_sra_to_s3("SRR390728", "my-bucket")


def test_copy_sra_to_s3_propagates_boto3_errors():
    pytest.importorskip("boto3")
    from botocore.exceptions import ClientError

    s3_client = MagicMock()
    s3_client.copy.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "CopyObject")

    with pytest.raises(ClientError):
        sra.copy_sra_to_s3("SRR390728", "my-bucket", s3_client=s3_client)
