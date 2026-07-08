from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree

from ncbi_client.throttle import NCBIAPIError

try:
    import paramiko
except ImportError:
    paramiko = None

# NCBI's UI-less Submission Protocol derives an aggregate submission status
# from per-action statuses by this precedence (most to least severe); see
# docs/UI-lessSubmissionProtocol.md Appendix A. We trust this derivation over
# the report's own top-level SubmissionStatus/@status, which isn't guaranteed
# present or correct.
_STATUS_PRECEDENCE = ["Processed-error", "Processing", "Queued", "Deleted", "Processed-ok"]

_REPORT_NAME_RE = re.compile(r"^report\.(\d+)\.xml$")


class SubmissionError(NCBIAPIError):
    pass


@dataclass
class Contact:
    email: str
    first_name: str
    last_name: str


@dataclass
class Organization:
    name: str
    contact: Contact | None = None
    role: str = "owner"
    org_type: str = "institute"


@dataclass
class BioSampleSubmission:
    spuid: str
    spuid_namespace: str
    organism_name: str
    package: str
    attributes: dict[str, str] = field(default_factory=dict)
    title: str | None = None
    bioproject_accession: str | None = None


@dataclass
class ActionResult:
    status: str
    target_db: str | None
    accession: str | None
    spuid: str | None
    spuid_namespace: str | None
    messages: list[str]


@dataclass
class SubmissionResult:
    status: str
    submission_id: str | None
    actions: list[ActionResult]
    messages: list[str]
    report_path: str
    raw_xml: bytes


@dataclass
class SubmissionHandle:
    host: str
    remote_folder: str
    submission_xml: bytes


# --- XML builder ---


def _build_description(organization: Organization, *, comment: str | None, hold_release_date: str | None) -> ElementTree.Element:
    description = ElementTree.Element("Description")

    if comment:
        comment_el = ElementTree.SubElement(description, "Comment")
        comment_el.text = comment

    org_el = ElementTree.SubElement(
        description, "Organization", {"role": organization.role, "type": organization.org_type}
    )
    name_el = ElementTree.SubElement(org_el, "Name")
    name_el.text = organization.name

    if organization.contact:
        contact_el = ElementTree.SubElement(org_el, "Contact", {"email": organization.contact.email})
        contact_name_el = ElementTree.SubElement(contact_el, "Name")
        first_el = ElementTree.SubElement(contact_name_el, "First")
        first_el.text = organization.contact.first_name
        last_el = ElementTree.SubElement(contact_name_el, "Last")
        last_el.text = organization.contact.last_name

    if hold_release_date:
        ElementTree.SubElement(description, "Hold", {"release_date": hold_release_date})

    return description


def _build_biosample_add_data_action(biosample: BioSampleSubmission) -> ElementTree.Element:
    action = ElementTree.Element("Action")
    add_data = ElementTree.SubElement(action, "AddData", {"target_db": "BioSample"})
    data = ElementTree.SubElement(add_data, "Data", {"content_type": "XML"})
    xml_content = ElementTree.SubElement(data, "XmlContent")

    biosample_el = ElementTree.SubElement(xml_content, "BioSample", {"schema_version": "2.0"})

    sample_id = ElementTree.SubElement(biosample_el, "SampleId")
    spuid_el = ElementTree.SubElement(sample_id, "SPUID", {"spuid_namespace": biosample.spuid_namespace})
    spuid_el.text = biosample.spuid

    if biosample.title:
        descriptor = ElementTree.SubElement(biosample_el, "Descriptor")
        title_el = ElementTree.SubElement(descriptor, "Title")
        title_el.text = biosample.title

    organism = ElementTree.SubElement(biosample_el, "Organism")
    organism_name_el = ElementTree.SubElement(organism, "OrganismName")
    organism_name_el.text = biosample.organism_name

    if biosample.bioproject_accession:
        bioproject = ElementTree.SubElement(biosample_el, "BioProject")
        primary_id = ElementTree.SubElement(bioproject, "PrimaryId", {"db": "BioProject"})
        primary_id.text = biosample.bioproject_accession

    package_el = ElementTree.SubElement(biosample_el, "Package")
    package_el.text = biosample.package

    attributes_el = ElementTree.SubElement(biosample_el, "Attributes")
    for name, value in biosample.attributes.items():
        attribute_el = ElementTree.SubElement(attributes_el, "Attribute", {"attribute_name": name})
        attribute_el.text = value

    identifier = ElementTree.SubElement(add_data, "Identifier")
    identifier_spuid = ElementTree.SubElement(identifier, "SPUID", {"spuid_namespace": biosample.spuid_namespace})
    identifier_spuid.text = biosample.spuid

    return action


def build_biosample_submission_xml(
    organization: Organization,
    biosamples: list[BioSampleSubmission],
    *,
    comment: str | None = None,
    hold_release_date: str | None = None,
) -> ElementTree.Element:
    """Build a <Submission> envelope with one <Action><AddData target_db="BioSample">
    per BioSample. No SRA/BioProject creation actions - BioSample-only for this cut.
    """
    root = ElementTree.Element("Submission")
    root.append(_build_description(organization, comment=comment, hold_release_date=hold_release_date))
    for biosample in biosamples:
        root.append(_build_biosample_add_data_action(biosample))
    return root


def submission_xml_bytes(root: ElementTree.Element) -> bytes:
    ElementTree.indent(root)
    return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)


# --- SFTP transport ---


def _require_paramiko():
    if paramiko is None:
        raise SubmissionError(
            "This operation requires paramiko, which is not installed. "
            "Install it with: pip install ncbi-client[sftp] "
            "(or pass a pre-connected sftp_client=...)"
        )


def _connect_sftp(
    host: str,
    username: str | None,
    password: str | None,
    *,
    port: int = 22,
    auto_add_host_key: bool = False,
    known_hosts_path: str | None = None,
    timeout: float = 10.0,
):
    """Connect to `host` over SFTP (paramiko/SSH).

    Defaults to paramiko.RejectPolicy for unknown host keys - the caller must
    have the host key already trusted (system known_hosts, or an explicit
    known_hosts_path), or pass auto_add_host_key=True to opt into trust-on-
    first-use. This is stricter than trust-on-first-use-by-default since this
    module creates real external state.
    """
    _require_paramiko()
    ssh = paramiko.SSHClient()
    if known_hosts_path:
        ssh.load_host_keys(known_hosts_path)
    else:
        ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy() if auto_add_host_key else paramiko.RejectPolicy())
    # gss_auth/gss_kex (present in older paramiko releases) were dropped from
    # SSHClient.connect() in paramiko 5.0 - omitted here rather than passed as
    # False, since GSSAPI auth isn't attempted unless explicitly requested
    # anyway, and this keeps the call compatible across paramiko versions.
    ssh.connect(
        host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return ssh, ssh.open_sftp()


def _default_folder_name(suffix: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{timestamp}-submission"
    if suffix:
        name += f"-{suffix}"
    return name


def _upload_submission(sftp, remote_folder: str, xml_bytes: bytes) -> None:
    sftp.mkdir(remote_folder)

    xml_path = posixpath.join(remote_folder, "submission.xml")
    with sftp.open(xml_path, "wb") as f:
        f.write(xml_bytes)
    if sftp.stat(xml_path).st_size != len(xml_bytes):
        raise SubmissionError(f"submission.xml upload size mismatch at {xml_path!r}")

    # submit.ready is uploaded last and only after the size check above
    # passes, so NCBI's scanner can never observe a submit.ready next to an
    # incomplete submission.xml.
    ready_path = posixpath.join(remote_folder, "submit.ready")
    with sftp.open(ready_path, "wb") as f:
        f.write(b"")


def submit_biosamples(
    biosamples: list[BioSampleSubmission],
    organization: Organization,
    *,
    host: str,
    remote_base_path: str,
    username: str | None = None,
    password: str | None = None,
    port: int = 22,
    sftp_client=None,
    auto_add_host_key: bool = False,
    known_hosts_path: str | None = None,
    folder_name: str | None = None,
    hold_release_date: str | None = None,
    comment: str | None = None,
) -> SubmissionHandle:
    """Submit BioSample creation action(s) via NCBI's UI-less Submission Protocol.

    `host` and `remote_base_path` are required with no defaults - there is no
    "default" NCBI server this will silently reach. Pass a pre-connected
    `sftp_client` for full control over the connection (recommended for
    anything beyond ad hoc scripts); otherwise one is created and used only
    for this call.
    """
    root = build_biosample_submission_xml(organization, biosamples, comment=comment, hold_release_date=hold_release_date)
    xml_bytes = submission_xml_bytes(root)

    folder_name = folder_name or _default_folder_name()
    remote_folder = posixpath.join(remote_base_path, folder_name)

    if sftp_client is not None:
        _upload_submission(sftp_client, remote_folder, xml_bytes)
    else:
        ssh, sftp = _connect_sftp(
            host, username, password, port=port, auto_add_host_key=auto_add_host_key, known_hosts_path=known_hosts_path
        )
        try:
            _upload_submission(sftp, remote_folder, xml_bytes)
        finally:
            sftp.close()
            ssh.close()

    return SubmissionHandle(host=host, remote_folder=remote_folder, submission_xml=xml_bytes)


# --- Report polling ---


def _latest_report_name(filenames: list[str]) -> str | None:
    numbered = []
    for name in filenames:
        m = _REPORT_NAME_RE.match(name)
        if m:
            numbered.append((int(m.group(1)), name))
    if not numbered:
        return None
    return max(numbered, key=lambda pair: pair[0])[1]


def _parse_action_result(action_el: ElementTree.Element) -> ActionResult:
    # The protocol doc documents Object/@accession, @url, @spuid_namespace,
    # @spuid explicitly, but doesn't confirm whether @target_db is echoed on
    # Action or Object in the response (only that Action/@status exists) -
    # check both, preferring Action's own attribute since that most directly
    # mirrors the target_db given in the original request.
    response = action_el.find("Response")
    obj = response.find("Object") if response is not None else None
    messages = [m.text or "" for m in (response.findall("Message") if response is not None else [])]
    return ActionResult(
        status=action_el.get("status", ""),
        target_db=action_el.get("target_db") or (obj.get("target_db") if obj is not None else None),
        accession=obj.get("accession") if obj is not None else None,
        spuid=obj.get("spuid") if obj is not None else None,
        spuid_namespace=obj.get("spuid_namespace") if obj is not None else None,
        messages=messages,
    )


def _derive_submission_status(actions: list[ActionResult]) -> str:
    statuses = {a.status for a in actions}
    for status in _STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return "Submitted"


def _parse_report(report_xml: bytes, report_path: str) -> SubmissionResult:
    try:
        root = ElementTree.fromstring(report_xml)
    except ElementTree.ParseError as e:
        raise SubmissionError(f"{report_path} is not valid XML: {report_xml!r}") from e

    submission_id = root.get("submission_id")
    top_level_messages = [m.text or "" for m in root.findall("Message")]
    actions = [_parse_action_result(a) for a in root.findall("Action")]

    return SubmissionResult(
        status=_derive_submission_status(actions),
        submission_id=submission_id,
        actions=actions,
        messages=top_level_messages,
        report_path=report_path,
        raw_xml=report_xml,
    )


def poll_submission_report(
    *,
    host: str,
    remote_folder: str,
    username: str | None = None,
    password: str | None = None,
    port: int = 22,
    sftp_client=None,
    auto_add_host_key: bool = False,
    known_hosts_path: str | None = None,
    poll_interval: float = 30.0,
    timeout: float | None = 3600.0,
) -> SubmissionResult:
    """Poll `remote_folder` for a report.<N>.xml and return once terminal.

    Raises SubmissionError on Processed-error/Deleted status, or on timeout
    while still Queued/Processing. timeout=None polls indefinitely.
    """
    owns_connection = sftp_client is None
    if owns_connection:
        ssh, sftp = _connect_sftp(
            host, username, password, port=port, auto_add_host_key=auto_add_host_key, known_hosts_path=known_hosts_path
        )
    else:
        sftp = sftp_client

    try:
        start = time.monotonic()
        while True:
            filenames = sftp.listdir(remote_folder)
            report_name = _latest_report_name(filenames)
            if report_name is not None:
                report_path = posixpath.join(remote_folder, report_name)
                with sftp.open(report_path, "rb") as f:
                    report_xml = f.read()
                result = _parse_report(report_xml, report_path)

                if result.status == "Processed-ok":
                    return result
                if result.status in ("Processed-error", "Deleted"):
                    error = SubmissionError(f"Submission at {remote_folder!r} ended with status {result.status!r}")
                    error.result = result
                    raise error

            if timeout is not None and (time.monotonic() - start) >= timeout:
                raise SubmissionError(
                    f"Timed out after {timeout}s waiting for a terminal report status at {remote_folder!r}"
                )
            time.sleep(poll_interval)
    finally:
        if owns_connection:
            sftp.close()
            ssh.close()


def submit_and_wait(
    biosamples: list[BioSampleSubmission],
    organization: Organization,
    *,
    host: str,
    remote_base_path: str,
    username: str | None = None,
    password: str | None = None,
    port: int = 22,
    sftp_client=None,
    auto_add_host_key: bool = False,
    known_hosts_path: str | None = None,
    folder_name: str | None = None,
    hold_release_date: str | None = None,
    comment: str | None = None,
    poll_interval: float = 30.0,
    timeout: float | None = 3600.0,
) -> SubmissionResult:
    """Convenience: submit_biosamples() + poll_submission_report() over one connection."""
    owns_connection = sftp_client is None
    if owns_connection:
        ssh, sftp = _connect_sftp(
            host, username, password, port=port, auto_add_host_key=auto_add_host_key, known_hosts_path=known_hosts_path
        )
    else:
        sftp = sftp_client

    try:
        handle = submit_biosamples(
            biosamples,
            organization,
            host=host,
            remote_base_path=remote_base_path,
            sftp_client=sftp,
            folder_name=folder_name,
            hold_release_date=hold_release_date,
            comment=comment,
        )
        return poll_submission_report(
            host=host,
            remote_folder=handle.remote_folder,
            sftp_client=sftp,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    finally:
        if owns_connection:
            sftp.close()
            ssh.close()
