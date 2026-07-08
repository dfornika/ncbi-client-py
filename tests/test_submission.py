from unittest.mock import MagicMock

import pytest

from ncbi_client import submission

# --- XML builder ---


def test_build_biosample_submission_xml_matches_vendored_example_shape():
    org = submission.Organization(
        name="Institute of Biology", contact=submission.Contact("jane.doe@domain.com", "jane", "doe")
    )
    bs = submission.BioSampleSubmission(
        spuid="CIES-13-0265",
        spuid_namespace="CFSAN",
        organism_name="Salmonella enterica subsp. enterica",
        package="Pathogen.env.1.0",
        attributes={"strain": "CIES-13-0265", "collection_date": "2013-05-13"},
        title="Pathogen sample from Salmonella enterica",
        bioproject_accession="PRJNA217342",
    )

    root = submission.build_biosample_submission_xml(org, [bs])

    assert root.tag == "Submission"
    description = root.find("Description")
    org_el = description.find("Organization")
    assert org_el.get("role") == "owner"
    assert org_el.get("type") == "institute"
    assert org_el.find("Name").text == "Institute of Biology"
    contact_el = org_el.find("Contact")
    assert contact_el.get("email") == "jane.doe@domain.com"
    assert contact_el.find("Name/First").text == "jane"
    assert contact_el.find("Name/Last").text == "doe"

    action = root.find("Action")
    add_data = action.find("AddData")
    assert add_data.get("target_db") == "BioSample"
    biosample_el = add_data.find("Data/XmlContent/BioSample")
    assert biosample_el.get("schema_version") == "2.0"
    assert biosample_el.find("SampleId/SPUID").text == "CIES-13-0265"
    assert biosample_el.find("SampleId/SPUID").get("spuid_namespace") == "CFSAN"
    assert biosample_el.find("Descriptor/Title").text == "Pathogen sample from Salmonella enterica"
    assert biosample_el.find("Organism/OrganismName").text == "Salmonella enterica subsp. enterica"
    assert biosample_el.find("BioProject/PrimaryId").text == "PRJNA217342"
    assert biosample_el.find("BioProject/PrimaryId").get("db") == "BioProject"
    assert biosample_el.find("Package").text == "Pathogen.env.1.0"
    attrs = biosample_el.find("Attributes").findall("Attribute")
    assert [(a.get("attribute_name"), a.text) for a in attrs] == [
        ("strain", "CIES-13-0265"),
        ("collection_date", "2013-05-13"),
    ]
    identifier = add_data.find("Identifier/SPUID")
    assert identifier.text == "CIES-13-0265"
    assert identifier.get("spuid_namespace") == "CFSAN"


def test_build_biosample_submission_xml_omits_optional_fields():
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    root = submission.build_biosample_submission_xml(org, [bs])

    description = root.find("Description")
    assert description.find("Comment") is None
    assert description.find("Hold") is None
    assert description.find("Organization/Contact") is None
    biosample_el = root.find("Action/AddData/Data/XmlContent/BioSample")
    assert biosample_el.find("Descriptor") is None
    assert biosample_el.find("BioProject") is None


def test_build_biosample_submission_xml_includes_comment_and_hold():
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    root = submission.build_biosample_submission_xml(
        org, [bs], comment="BP(1.0)+BS(1.0)+SRA", hold_release_date="2018-10-21"
    )

    description = root.find("Description")
    assert description.find("Comment").text == "BP(1.0)+BS(1.0)+SRA"
    assert description.find("Hold").get("release_date") == "2018-10-21"


def test_build_biosample_submission_xml_multiple_samples():
    org = submission.Organization(name="Institute of Biology")
    samples = [
        submission.BioSampleSubmission(
            spuid=f"s{i}", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0",
            attributes={},
        )
        for i in range(3)
    ]

    root = submission.build_biosample_submission_xml(org, samples)

    actions = root.findall("Action")
    assert len(actions) == 3
    spuids = [a.find("AddData/Identifier/SPUID").text for a in actions]
    assert spuids == ["s0", "s1", "s2"]


# --- Upload sequencing ---


def test_submit_biosamples_upload_order(monkeypatch):
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    sftp_client = MagicMock()
    sftp_client.open.return_value.__enter__.return_value = MagicMock()
    sftp_client.stat.return_value.st_size = len(
        submission.submission_xml_bytes(submission.build_biosample_submission_xml(org, [bs]))
    )

    handle = submission.submit_biosamples(
        [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser",
        sftp_client=sftp_client, folder_name="20260101T000000Z-submission",
    )

    remote_folder = "uploads/testuser/20260101T000000Z-submission"
    assert handle.remote_folder == remote_folder
    sftp_client.mkdir.assert_called_once_with(remote_folder)

    open_calls = [c.args[0] for c in sftp_client.open.call_args_list]
    assert open_calls == [f"{remote_folder}/submission.xml", f"{remote_folder}/submit.ready"]


def test_submit_biosamples_raises_on_size_mismatch():
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    sftp_client = MagicMock()
    sftp_client.open.return_value.__enter__.return_value = MagicMock()
    sftp_client.stat.return_value.st_size = 1  # wrong on purpose

    with pytest.raises(submission.SubmissionError, match="size mismatch"):
        submission.submit_biosamples(
            [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser", sftp_client=sftp_client
        )

    # submit.ready must never be uploaded after a failed size check
    open_calls = [c.args[0] for c in sftp_client.open.call_args_list]
    assert not any(p.endswith("submit.ready") for p in open_calls)


def test_submit_biosamples_rolls_back_remote_folder_on_failure():
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    sftp_client = MagicMock()
    sftp_client.open.return_value.__enter__.return_value = MagicMock()
    sftp_client.stat.return_value.st_size = 1  # forces the size-mismatch failure
    sftp_client.listdir.return_value = ["submission.xml"]

    with pytest.raises(submission.SubmissionError, match="size mismatch"):
        submission.submit_biosamples(
            [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser",
            sftp_client=sftp_client, folder_name="20260101T000000Z-submission",
        )

    remote_folder = "uploads/testuser/20260101T000000Z-submission"
    sftp_client.remove.assert_called_once_with(f"{remote_folder}/submission.xml")
    sftp_client.rmdir.assert_called_once_with(remote_folder)


def test_submit_biosamples_wraps_mkdir_error():
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )

    sftp_client = MagicMock()
    sftp_client.mkdir.side_effect = OSError("already exists")

    with pytest.raises(submission.SubmissionError, match="Could not create remote folder"):
        submission.submit_biosamples(
            [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser", sftp_client=sftp_client
        )


def test_submit_biosamples_forwards_connect_timeout(monkeypatch):
    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )
    xml_bytes = submission.submission_xml_bytes(submission.build_biosample_submission_xml(org, [bs]))

    fake_ssh = MagicMock()
    fake_sftp = MagicMock()
    fake_sftp.open.return_value.__enter__.return_value = MagicMock()
    fake_sftp.stat.return_value.st_size = len(xml_bytes)
    mock_connect = MagicMock(return_value=(fake_ssh, fake_sftp))
    monkeypatch.setattr(submission, "_connect_sftp", mock_connect)

    submission.submit_biosamples(
        [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser", connect_timeout=42.0
    )

    assert mock_connect.call_args.kwargs["timeout"] == 42.0
    fake_sftp.close.assert_called_once()
    fake_ssh.close.assert_called_once()


# --- Report parsing ---


def _report_xml(actions_xml: str, *, submission_id: str | None = "SUB12345") -> bytes:
    attr = f' submission_id="{submission_id}"' if submission_id else ""
    return f"<SubmissionStatus{attr}>{actions_xml}</SubmissionStatus>".encode()


def test_parse_report_processed_ok_with_accession():
    xml = _report_xml(
        '<Action status="Processed-ok" target_db="BioSample">'
        '<Response><Object accession="SAMN12345678" spuid="s1" spuid_namespace="NS"/></Response>'
        "</Action>"
    )
    result = submission._parse_report(xml, "report.1.xml")

    assert result.status == "Processed-ok"
    assert result.submission_id == "SUB12345"
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.status == "Processed-ok"
    assert action.accession == "SAMN12345678"
    assert action.spuid == "s1"
    assert action.spuid_namespace == "NS"


def test_parse_report_processed_error_with_message():
    xml = _report_xml(
        '<Action status="Processed-error"><Response><Message>Missing required attribute: strain</Message></Response></Action>'
    )
    result = submission._parse_report(xml, "report.1.xml")

    assert result.status == "Processed-error"
    assert result.actions[0].messages == ["Missing required attribute: strain"]


@pytest.mark.parametrize("status", ["Queued", "Processing", "Deleted"])
def test_parse_report_single_status(status):
    xml = _report_xml(f'<Action status="{status}"/>')
    result = submission._parse_report(xml, "report.1.xml")
    assert result.status == status


def test_parse_report_status_precedence_error_wins():
    xml = _report_xml('<Action status="Processed-ok"/><Action status="Processed-error"/><Action status="Queued"/>')
    result = submission._parse_report(xml, "report.1.xml")
    assert result.status == "Processed-error"


def test_parse_report_status_precedence_processing_over_queued():
    xml = _report_xml('<Action status="Queued"/><Action status="Processing"/>')
    result = submission._parse_report(xml, "report.1.xml")
    assert result.status == "Processing"


def test_parse_report_status_precedence_all_ok_but_one_deleted():
    xml = _report_xml('<Action status="Processed-ok"/><Action status="Deleted"/>')
    result = submission._parse_report(xml, "report.1.xml")
    assert result.status == "Deleted"


def test_parse_report_malformed_xml_raises_clear_error():
    with pytest.raises(submission.SubmissionError, match="not valid XML"):
        submission._parse_report(b"<SubmissionStatus", "report.1.xml")


def test_parse_report_no_actions_raises_instead_of_returning_submitted():
    xml = _report_xml("")
    with pytest.raises(submission.SubmissionError, match="no <Action> elements"):
        submission._parse_report(xml, "report.1.xml")


# --- Polling loop ---


def test_poll_submission_report_waits_then_succeeds(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    sftp_client = MagicMock()
    processing_xml = _report_xml('<Action status="Processing"/>')
    ok_xml = _report_xml('<Action status="Processed-ok"><Response><Object accession="SAMN1"/></Response></Action>')

    listdir_results = [[], ["report.1.xml"], ["report.1.xml", "report.2.xml"]]
    sftp_client.listdir.side_effect = listdir_results

    open_returns = [processing_xml, ok_xml]

    def fake_open(path, mode):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = open_returns.pop(0)
        return cm

    sftp_client.open.side_effect = fake_open

    result = submission.poll_submission_report(
        host="sftp.example.com", remote_folder="uploads/testuser/x", sftp_client=sftp_client, poll_interval=0
    )

    assert result.status == "Processed-ok"
    assert result.actions[0].accession == "SAMN1"


def test_poll_submission_report_raises_on_processed_error(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    sftp_client = MagicMock()
    sftp_client.listdir.return_value = ["report.1.xml"]
    error_xml = _report_xml('<Action status="Processed-error"><Response><Message>bad</Message></Response></Action>')
    sftp_client.open.return_value.__enter__.return_value.read.return_value = error_xml

    with pytest.raises(submission.SubmissionError) as exc_info:
        submission.poll_submission_report(
            host="sftp.example.com", remote_folder="uploads/testuser/x", sftp_client=sftp_client, poll_interval=0
        )

    assert exc_info.value.result.status == "Processed-error"


def test_poll_submission_report_times_out(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    times = iter([0, 0, 10, 10])
    monkeypatch.setattr(submission.time, "monotonic", lambda: next(times))

    sftp_client = MagicMock()
    sftp_client.listdir.return_value = []

    with pytest.raises(submission.SubmissionError, match="Timed out"):
        submission.poll_submission_report(
            host="sftp.example.com", remote_folder="uploads/testuser/x", sftp_client=sftp_client,
            poll_interval=0, timeout=5,
        )


def test_poll_submission_report_wraps_listdir_error(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    sftp_client = MagicMock()
    sftp_client.listdir.side_effect = OSError("no such file")

    with pytest.raises(submission.SubmissionError, match="Could not list"):
        submission.poll_submission_report(
            host="sftp.example.com", remote_folder="uploads/testuser/missing", sftp_client=sftp_client, poll_interval=0
        )


def test_poll_submission_report_forwards_connect_timeout(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    fake_ssh = MagicMock()
    fake_sftp = MagicMock()
    fake_sftp.listdir.return_value = ["report.1.xml"]
    ok_xml = _report_xml('<Action status="Processed-ok"/>')
    fake_sftp.open.return_value.__enter__.return_value.read.return_value = ok_xml
    mock_connect = MagicMock(return_value=(fake_ssh, fake_sftp))
    monkeypatch.setattr(submission, "_connect_sftp", mock_connect)

    submission.poll_submission_report(
        host="sftp.example.com", remote_folder="uploads/testuser/x", connect_timeout=42.0, poll_interval=0
    )

    assert mock_connect.call_args.kwargs["timeout"] == 42.0


# --- Missing paramiko / real exception propagation ---


def test_connect_sftp_raises_actionable_error_without_paramiko(monkeypatch):
    monkeypatch.setattr(submission, "paramiko", None)
    with pytest.raises(submission.SubmissionError, match=r"pip install ncbi-client\[sftp\]"):
        submission._connect_sftp("sftp.example.com", "user", "pass")


def test_connect_sftp_propagates_real_paramiko_errors():
    pytest.importorskip("paramiko")
    with pytest.raises(Exception):
        submission._connect_sftp("127.0.0.1", "user", "pass", port=1, timeout=1)


def test_connect_sftp_closes_ssh_if_open_sftp_fails(monkeypatch):
    fake_ssh = MagicMock()
    fake_ssh.open_sftp.side_effect = OSError("sftp subsystem failed")
    fake_paramiko = MagicMock()
    fake_paramiko.SSHClient.return_value = fake_ssh
    monkeypatch.setattr(submission, "paramiko", fake_paramiko)

    with pytest.raises(OSError, match="sftp subsystem failed"):
        submission._connect_sftp("sftp.example.com", "user", "pass")

    fake_ssh.close.assert_called_once()


# --- _sftp_session connection lifecycle ---


def test_sftp_session_uses_injected_client_without_closing():
    sftp_client = MagicMock()
    with submission._sftp_session(
        "h", "u", "p", port=22, sftp_client=sftp_client, auto_add_host_key=False,
        known_hosts_path=None, connect_timeout=10.0,
    ) as sftp:
        assert sftp is sftp_client
    sftp_client.close.assert_not_called()


def test_sftp_session_closes_owned_connection_on_exception(monkeypatch):
    fake_ssh = MagicMock()
    fake_sftp = MagicMock()
    monkeypatch.setattr(submission, "_connect_sftp", MagicMock(return_value=(fake_ssh, fake_sftp)))

    with pytest.raises(ValueError, match="boom"):
        with submission._sftp_session(
            "h", "u", "p", port=22, sftp_client=None, auto_add_host_key=False,
            known_hosts_path=None, connect_timeout=10.0,
        ):
            raise ValueError("boom")

    fake_sftp.close.assert_called_once()
    fake_ssh.close.assert_called_once()


# --- submit_and_wait ---


def test_submit_and_wait_success_reuses_one_connection(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )
    xml_bytes = submission.submission_xml_bytes(submission.build_biosample_submission_xml(org, [bs]))
    ok_xml = _report_xml('<Action status="Processed-ok"><Response><Object accession="SAMN1"/></Response></Action>')

    sftp_client = MagicMock()
    sftp_client.stat.return_value.st_size = len(xml_bytes)
    sftp_client.listdir.return_value = ["report.1.xml"]

    def fake_open(path, mode):
        cm = MagicMock()
        if mode == "rb":
            cm.__enter__.return_value.read.return_value = ok_xml
        return cm

    sftp_client.open.side_effect = fake_open

    result = submission.submit_and_wait(
        [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser",
        sftp_client=sftp_client, poll_interval=0,
    )

    assert result.status == "Processed-ok"
    assert result.actions[0].accession == "SAMN1"
    # caller-supplied client - submit_and_wait must not close it
    sftp_client.close.assert_not_called()


def test_submit_and_wait_owns_and_closes_its_connection(monkeypatch):
    monkeypatch.setattr(submission.time, "sleep", lambda _: None)

    org = submission.Organization(name="Institute of Biology")
    bs = submission.BioSampleSubmission(
        spuid="s1", spuid_namespace="NS", organism_name="Escherichia coli", package="Pathogen.cl.1.0", attributes={}
    )
    xml_bytes = submission.submission_xml_bytes(submission.build_biosample_submission_xml(org, [bs]))
    ok_xml = _report_xml('<Action status="Processed-ok"/>')

    fake_ssh = MagicMock()
    fake_sftp = MagicMock()
    fake_sftp.stat.return_value.st_size = len(xml_bytes)
    fake_sftp.listdir.return_value = ["report.1.xml"]

    def fake_open(path, mode):
        cm = MagicMock()
        if mode == "rb":
            cm.__enter__.return_value.read.return_value = ok_xml
        return cm

    fake_sftp.open.side_effect = fake_open
    monkeypatch.setattr(submission, "_connect_sftp", MagicMock(return_value=(fake_ssh, fake_sftp)))

    result = submission.submit_and_wait(
        [bs], org, host="sftp.example.com", remote_base_path="uploads/testuser", poll_interval=0
    )

    assert result.status == "Processed-ok"
    fake_sftp.close.assert_called_once()
    fake_ssh.close.assert_called_once()
