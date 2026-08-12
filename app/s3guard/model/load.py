from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Claude Sonnet 5 through the Australia geo inference profile.

    The au. prefix keeps inference inside Australian regions
    (Sydney and Melbourne). Use global.anthropic.claude-sonnet-5 if you
    are deploying outside Australia. The AWS region and credentials come
    from the environment: locally from your AWS profile, on AgentCore
    Runtime from the execution role.

    Do not pass temperature here: Claude Sonnet 5 rejects it with a
    ValidationException because sampling parameters are deprecated for
    adaptive reasoning models.
    """
    return BedrockModel(model_id="au.anthropic.claude-sonnet-5")
