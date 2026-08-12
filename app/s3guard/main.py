"""S3 Guard: an S3 security auditor agent with approval-gated remediation.

Read tools run on every invocation. The single write tool
(enable_block_public_access) only executes when the caller sent
approve_writes=true in the payload. The gate is enforced in code,
not in the prompt, so the model cannot talk its way past it.
"""

from collections import OrderedDict

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from strands import Agent, tool

from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger

s3 = boto3.client("s3")

# Set per invocation from the payload. Each AgentCore Runtime session runs in
# its own microVM, so a module-level flag cannot leak across sessions.
_writes_approved = False

SYSTEM_PROMPT = """\
You are S3 Guard, an AWS S3 security auditor.

Workflow:
1. Use list_buckets to see what exists, then audit_bucket on each bucket \
the user asked about (or all of them if unspecified).
2. Report findings ranked by severity. Public access exposure is critical. \
Missing versioning is informational.
3. If the user asks you to fix a finding, call enable_block_public_access. \
If the tool reports DENIED, tell the user exactly how to approve the action. \
Never claim a fix was applied unless the tool returned success.

Be concise. Report facts from the tools, never invent findings.
"""


@tool
def list_buckets() -> list[str]:
    """List the names of all S3 buckets in the account."""
    return [b["Name"] for b in s3.list_buckets()["Buckets"]]


@tool
def audit_bucket(bucket_name: str) -> dict:
    """Audit one S3 bucket for public access exposure, encryption and versioning.

    Returns a dict of findings. public_access_block shows which of the four
    Block Public Access settings are enabled. policy_is_public is True when
    the bucket policy makes the bucket public.
    """
    findings: dict = {"bucket": bucket_name}

    try:
        config = s3.get_public_access_block(Bucket=bucket_name)[
            "PublicAccessBlockConfiguration"
        ]
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
            raise
        config = {}
    findings["public_access_block"] = {
        setting: config.get(setting, False)
        for setting in (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
    }

    try:
        status = s3.get_bucket_policy_status(Bucket=bucket_name)["PolicyStatus"]
        findings["policy_is_public"] = status["IsPublic"]
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchBucketPolicy":
            raise
        findings["policy_is_public"] = False

    acl = s3.get_bucket_acl(Bucket=bucket_name)
    findings["acl_public_grants"] = [
        grant["Permission"]
        for grant in acl["Grants"]
        if grant["Grantee"].get("URI", "").endswith(("AllUsers", "AuthenticatedUsers"))
    ]

    encryption = s3.get_bucket_encryption(Bucket=bucket_name)
    findings["encryption"] = encryption["ServerSideEncryptionConfiguration"]["Rules"][
        0
    ]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]

    versioning = s3.get_bucket_versioning(Bucket=bucket_name)
    findings["versioning"] = versioning.get("Status", "Disabled")

    return findings


@tool
def enable_block_public_access(bucket_name: str) -> str:
    """Enable all four S3 Block Public Access settings on a bucket.

    This is a write action. It only executes when the caller approved
    writes for this invocation.
    """
    if not _writes_approved:
        return (
            "DENIED: the caller has not approved write actions. Do not retry. "
            "Tell the user to re-invoke with approve_writes set to true, "
            f"for example: {{\"prompt\": \"enable block public access on "
            f"{bucket_name}\", \"approve_writes\": true}}"
        )
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return f"SUCCESS: all four Block Public Access settings enabled on {bucket_name}"


# One Agent per session so follow-up prompts keep their conversation history.
# Bounded LRU so a long-lived process cannot grow without limit.
_sessions: OrderedDict[str, Agent] = OrderedDict()


def get_or_create_agent(session_id: str) -> Agent:
    if session_id in _sessions:
        _sessions.move_to_end(session_id)
        return _sessions[session_id]
    if len(_sessions) >= 128:
        _sessions.popitem(last=False)
    _sessions[session_id] = Agent(
        model=load_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[list_buckets, audit_bucket, enable_block_public_access],
    )
    return _sessions[session_id]


@app.entrypoint
async def invoke(payload, context):
    global _writes_approved
    _writes_approved = bool(payload.get("approve_writes", False))
    log.info("Invocation received, writes_approved=%s", _writes_approved)

    agent = get_or_create_agent(getattr(context, "session_id", "default-session"))

    async for event in agent.stream_async(payload.get("prompt", "")):
        if isinstance(event, dict) and "event" in event:
            yield event


if __name__ == "__main__":
    app.run()
