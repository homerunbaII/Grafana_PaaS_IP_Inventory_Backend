from pydantic import BaseModel, field_validator


class IpInventoryBaseRequest(BaseModel):
    cluster: str
    bearer_token: str

    @field_validator("cluster")
    @classmethod
    def validate_cluster(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("cluster must be a string")

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cluster must not be empty")

        return cleaned

    @field_validator("bearer_token")
    @classmethod
    def validate_bearer_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("bearer_token must be a string")

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("bearer_token must not be empty")

        return cleaned


class IpInventoryListRequest(IpInventoryBaseRequest):
    pass


class IpInventoryLookupRequest(IpInventoryBaseRequest):
    ip: str

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("ip must be a string")

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("ip must not be empty")

        return cleaned
