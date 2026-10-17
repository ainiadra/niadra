"""Subject tokens: how an MCP connection or a tool kit is bound to one customer (5.17, 16.3)."""

from datetime import datetime

from pydantic import Field

from niadra.contracts.common import Handle, IdStr, Model
from niadra.domain.vocabulary import Verification


class SubjectTokenRequest(Model):
    subject: Handle
    about: Handle | None = Field(
        default=None,
        description="The organization the customer acts for (21.4), bound like the subject: tools use it and "
        "the model cannot change it. Every read still checks the link.",
    )
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    verification: Verification = Field(
        default=Verification.V0,
        description="The level the conversation claims; never above the source's ceiling.",
    )


class SubjectTokenResponse(Model):
    token: str = Field(description="Signed, valid for 15 minutes; send it as `Niadra-Subject-Token` to /mcp.")
    expires_at: datetime
