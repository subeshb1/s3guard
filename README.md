# S3 Guard

An AI agent that audits S3 buckets for security misconfigurations and fixes them, but only when a human approves. Built with [Strands Agents](https://strandsagents.com/) and deployed on [Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) with the current `@aws/agentcore` CLI.

Companion repo for the blog post: [Building an AI agent on AWS in 2026](https://www.subeshbhandari.com/blog/building-an-ai-agent-on-aws-with-bedrock-agentcore-and-strands).

## What it does

- **Audits**: lists buckets, checks Block Public Access, bucket policy status, ACL grants, encryption, and versioning.
- **Reports**: findings ranked by severity.
- **Remediates behind a gate**: the only write tool (`enable_block_public_access`) refuses to run unless the invocation payload contains `"approve_writes": true`. The gate is code, not prompt instructions, so the model cannot bypass it.
- **Least privilege**: the runtime execution role gets exactly the six read calls the audit makes, and the write action is scoped to `arn:aws:s3:::s3guard-demo-*` buckets only (see `agentcore/cdk/lib/cdk-stack.ts`).

## Architecture

```
caller (CLI / boto3, IAM auth)
   │  {"prompt": "...", "approve_writes": false}
   ▼
AgentCore Runtime (microVM per session, CodeZip deploy)
   └─ Strands Agent ── Claude Sonnet 5 (au. geo inference profile, Bedrock)
        ├─ list_buckets            (read)
        ├─ audit_bucket            (read)
        └─ enable_block_public_access  (write, approval-gated + IAM-scoped)
```

## Prerequisites

- Node.js 20+, Python 3.10+, [uv](https://docs.astral.sh/uv/)
- AWS account with Bedrock access to Claude Sonnet 5 (this project uses the `au.` inference profile; switch to `global.` in `app/s3guard/model/load.py` if you deploy outside Australia)
- AWS credentials with permission to deploy CDK stacks
- `npm install -g @aws/agentcore` and `npm install -g aws-cdk`

## Run locally

```bash
agentcore dev
```

Then invoke it (the chat UI also runs on localhost:8081):

```bash
curl -s -X POST http://127.0.0.1:8082/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Audit all my buckets.", "approve_writes": false}'
```

## Deploy

```bash
cdk bootstrap            # once per account/region
agentcore deploy
```

Invoke the deployed agent:

```bash
agentcore invoke '{"prompt": "Audit all my buckets."}'
```

Or from Python with boto3 (`bedrock-agentcore` client, `InvokeAgentRuntime`), see the blog post.

## Approving a fix

Reads never need approval. To let the agent apply a fix, pass the flag explicitly:

```bash
agentcore invoke '{"prompt": "Enable block public access on <bucket>.", "approve_writes": true}'
```

Without the flag the tool returns `DENIED` and the agent reports what it would have done instead.

## Cost

Serverless and consumption-based. The full build in the blog post (all local testing, deploy, several cloud invocations, teardown) cost about 24 US cents, over 95 percent of it Claude Sonnet 5 tokens. AgentCore Runtime bills per second of active compute only, so an idle deployed agent costs zero. Still, do not skip the cleanup.

## Cleanup

The CLI has no destroy command; the deploy is one CloudFormation stack, so delete that:

```bash
aws cloudformation delete-stack --stack-name AgentCore-s3guard-default
aws cloudformation wait stack-delete-complete --stack-name AgentCore-s3guard-default
```

Two things survive the stack delete, so remove them too for zero residue:

```bash
# any demo buckets you created
aws s3 rb s3://<your-demo-bucket> --force

# the runtime's retained log group
aws logs delete-log-group --log-group-name "/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT"
```

## Security notes

- No credentials are stored anywhere in this repo. Locally the CLI uses your AWS profile; deployed, the runtime uses its execution role.
- `agentcore/.env.local` is gitignored; never commit environment files.
- The IAM policy scoping the write action to the `s3guard-demo-*` prefix is deliberate. Widen it consciously, not by default.
