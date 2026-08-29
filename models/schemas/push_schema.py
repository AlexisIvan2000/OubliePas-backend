from pydantic import BaseModel, ConfigDict, Field

MAX_ENDPOINT_LENGTH = 2048
MAX_KEY_LENGTH = 255


class PushKeyResponse(BaseModel):
    # Vide quand le serveur n'a pas de paire : le client sait alors se taire
    # plutot que de proposer un interrupteur qui ne peut rien faire.
    public_key: str | None


class PushSubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)
    p256dh: str = Field(min_length=1, max_length=MAX_KEY_LENGTH)
    auth: str = Field(min_length=1, max_length=MAX_KEY_LENGTH)
    user_agent: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)


class PushUnsubscribe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)


class PushSubscriptionResponse(BaseModel):
    endpoint: str
    enabled: bool
