#!/usr/bin/env python
"""Scratchpad for playing with BioSample submission against a local test stack.

NOT part of the shipped library, NOT a pytest test - just a runnable script.

Prerequisites (see tests/manual/fake_submission_portal.py's docstring for more):

    mkdir -p /tmp/ncbi_sftp_test/uploads/testuser
    chmod -R 777 /tmp/ncbi_sftp_test
    docker run -d --name ncbi-client-test-sftp -p 2222:22 \\
        -v /tmp/ncbi_sftp_test/uploads:/home/testuser/uploads \\
        atmoz/sftp testuser:testpass:1000:1000:uploads

    python tests/manual/fake_submission_portal.py /tmp/ncbi_sftp_test/uploads --interval 1

Then just run this script:

    python tests/manual/try_submit_biosample.py

This talks ONLY to the local Docker container above - never to any real NCBI
server. host/remote_base_path have no library defaults, so there's no way
this accidentally reaches anything real.
"""

from datetime import datetime, timezone

from ncbi_client import NCBIClient
from ncbi_client.submission import BioSampleSubmission, Contact, Organization

# Unique-ish per run so repeated invocations are easy to tell apart in the
# fake portal's log / on-disk report, though nothing here requires uniqueness.
run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

organization = Organization(
    name="Test Institute",
    contact=Contact(email="test@example.com", first_name="Test", last_name="User"),
)

biosamples = [
    BioSampleSubmission(
        spuid=f"try-script-{run_id}",
        spuid_namespace="NCBICLIENTPY",
        organism_name="Escherichia coli",
        package="Pathogen.cl.1.0",
        title="Playing with BioSample submission locally",
        attributes={
            "strain": "K-12",
            "collection_date": "2026-07-07",
            "geo_loc_name": "Canada",
            "isolation_source": "test script",
        },
    ),
]

client = NCBIClient()
try:
    result = client.submit_biosamples_and_wait(
        biosamples,
        organization,
        host="127.0.0.1",
        port=2222,
        username="testuser",
        password="testpass",
        remote_base_path="uploads/testuser",
        auto_add_host_key=True,  # local container's host key isn't in ~/.ssh/known_hosts
        poll_interval=1,
        timeout=30,
    )
finally:
    client.close()

print(f"submission status: {result.status}")
for action in result.actions:
    print(f"  action: status={action.status} accession={action.accession} spuid={action.spuid}")
