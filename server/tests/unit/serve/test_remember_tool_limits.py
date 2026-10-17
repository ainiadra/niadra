"""The `remember` tool offers a model the same note limits the API takes, so an agent following the tool
never writes a note the API refuses, nor stops short of what it keeps."""

from niadra.contracts.agent_memory import MAX_BODY, MAX_TAGS, MAX_TITLE
from niadra.domain.serve.tools import REMEMBER


def test_the_remember_tool_takes_what_the_api_takes() -> None:
    properties = REMEMBER.parameters["properties"]
    assert properties["title"]["maxLength"] == MAX_TITLE
    assert properties["body"]["maxLength"] == MAX_BODY
    assert properties["tags"]["maxItems"] == MAX_TAGS
