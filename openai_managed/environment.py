"""Application destinations for one Sasha deployment."""

import os
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urlsplit


def required_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def required_url(name: str) -> str:
    value = required_value(name)
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL without credentials or a fragment")
    return value


@dataclass(frozen=True)
class ApplicationEnvironment:
    name: str
    crm_base_url: str
    login_url: str
    catalog_url: str
    designs_url: str
    design_tool_url: str
    help_center_url: str

    @classmethod
    def from_environment(cls) -> "ApplicationEnvironment":
        name = required_value("FP_ENVIRONMENT")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError("FP_ENVIRONMENT must contain only lowercase letters, digits, underscores, or hyphens")
        crm_base_url = required_url("FP_BASE_URL").rstrip("/")
        if urlsplit(crm_base_url).query:
            raise ValueError("FP_BASE_URL must not contain a query")
        return cls(
            name=name,
            crm_base_url=crm_base_url,
            login_url=required_url("FP_LOGIN_URL"),
            catalog_url=required_url("FP_CATALOG_URL"),
            designs_url=required_url("FP_DESIGNS_URL"),
            design_tool_url=required_url("FP_DESIGN_TOOL_URL"),
            help_center_url=required_url("FP_HELP_CENTER_URL"),
        )

    def deal_url(self, deal_id: str) -> str:
        return f"{self.crm_base_url}/dashboard/sales-pipeline/deal?{urlencode({'id': deal_id})}"

    def validate_deal_url(self, deal_url: str) -> None:
        configured = urlsplit(self.crm_base_url)
        supplied = urlsplit(deal_url)
        if (supplied.scheme, supplied.netloc) != (configured.scheme, configured.netloc):
            raise ValueError("Deal URL must use the configured FP_BASE_URL origin")

    def task_context(self) -> str:
        return "\n".join([
            f"Environment: {self.name}",
            f"CRM base URL: {self.crm_base_url}",
            f"Login URL: {self.login_url}",
            f"Product catalog URL: {self.catalog_url}",
            f"Designs gallery URL: {self.designs_url}",
            f"Design Tool URL: {self.design_tool_url}",
            f"Help center URL: {self.help_center_url}",
        ])

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
