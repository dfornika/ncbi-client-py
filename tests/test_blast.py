import json

import pytest
import respx
from httpx import Response

from ncbi_client import blast

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

JSON2_S_RESPONSE = json.dumps({
    "BlastOutput2": [
        {
            "report": {
                "results": {
                    "search": {
                        "query_id": "Query_1",
                        "query_title": "my query",
                        "query_len": 30,
                        "hits": [
                            {
                                "len": 5000,
                                "description": [{"accession": "NM_000000", "title": "Some gene", "taxid": 9606}],
                                "hsps": [
                                    {
                                        "bit_score": 50.0,
                                        "score": 40,
                                        "evalue": 1e-10,
                                        "identity": 30,
                                        "align_len": 30,
                                        "gaps": 0,
                                        "query_from": 1,
                                        "query_to": 30,
                                        "hit_from": 100,
                                        "hit_to": 129,
                                        "qseq": "ACGT",
                                        "hseq": "ACGT",
                                        "midline": "||||",
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        }
    ]
})


def test_submit_extracts_rid_and_rtoe(client):
    with respx.mock:
        respx.post(BLAST_URL).mock(
            return_value=Response(200, text="stuff\n    RID = ABC123XYZ\n    RTOE = 15\nmore stuff")
        )
        result = blast.submit(client, "ACGT", program="blastn", database="core_nt")
    assert result == {"rid": "ABC123XYZ", "rtoe": 15}


def test_submit_rejects_unknown_program(client):
    with pytest.raises(ValueError):
        blast.submit(client, "ACGT", program="not-a-real-program", database="core_nt")


def test_submit_raises_when_no_rid_found(client):
    with respx.mock:
        respx.post(BLAST_URL).mock(return_value=Response(200, text="no rid here"))
        with pytest.raises(blast.BlastError):
            blast.submit(client, "ACGT", program="blastn", database="core_nt")


def test_status_ready(client):
    with respx.mock:
        respx.get(BLAST_URL).mock(
            return_value=Response(200, text="QBlastInfoBegin\n    Status=READY\nQBlastInfoEnd")
        )
        assert blast.status(client, "ABC123XYZ") == "READY"


def test_status_waiting(client):
    with respx.mock:
        respx.get(BLAST_URL).mock(
            return_value=Response(200, text="QBlastInfoBegin\n    Status=WAITING\nQBlastInfoEnd")
        )
        assert blast.status(client, "ABC123XYZ") == "WAITING"


def test_status_raises_when_missing(client):
    with respx.mock:
        respx.get(BLAST_URL).mock(return_value=Response(200, text="nothing useful"))
        with pytest.raises(blast.BlastError):
            blast.status(client, "ABC123XYZ")


def test_fetch_returns_raw_body(client):
    with respx.mock:
        respx.get(BLAST_URL).mock(return_value=Response(200, text=JSON2_S_RESPONSE))
        raw = blast.fetch(client, "ABC123XYZ")
    assert raw == JSON2_S_RESPONSE


def test_parse_json2(client):
    parsed = blast._parse_json2(JSON2_S_RESPONSE)
    assert len(parsed) == 1
    search_result = parsed[0]
    assert search_result["query_id"] == "Query_1"
    assert len(search_result["hits"]) == 1
    hit = search_result["hits"][0]
    assert hit["accession"] == "NM_000000"
    assert hit["taxid"] == 9606
    assert len(hit["hsps"]) == 1
    assert hit["hsps"][0]["evalue"] == 1e-10


def test_search_blocks_until_ready_and_returns_parsed_results(client, monkeypatch):
    monkeypatch.setattr(blast.time, "sleep", lambda _: None)

    statuses = iter(["WAITING", "READY"])

    with respx.mock:
        respx.post(BLAST_URL).mock(
            return_value=Response(200, text="RID = ABC123XYZ\nRTOE = 5\n")
        )

        def get_side_effect(request):
            params = dict(request.url.params)
            if params.get("FORMAT_OBJECT") == "SearchInfo":
                return Response(200, text=f"Status={next(statuses)}")
            return Response(200, text=JSON2_S_RESPONSE)

        respx.get(BLAST_URL).mock(side_effect=get_side_effect)

        result = blast.search(client, "ACGT", program="blastn", database="core_nt")

    assert result["rid"] == "ABC123XYZ"
    assert result["program"] == "blastn"
    assert len(result["searches"]) == 1


def test_search_raises_on_failed_status(client, monkeypatch):
    monkeypatch.setattr(blast.time, "sleep", lambda _: None)

    with respx.mock:
        respx.post(BLAST_URL).mock(return_value=Response(200, text="RID = ABC123XYZ\n"))
        respx.get(BLAST_URL).mock(return_value=Response(200, text="Status=FAILED"))

        with pytest.raises(blast.BlastError):
            blast.search(client, "ACGT", program="blastn", database="core_nt")
