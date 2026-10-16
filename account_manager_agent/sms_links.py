"""Shorten URLs in final SMS drafts without changing the agent's wording."""

import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from sasha_database.config import env_value


DEFAULT_SHORTEN_URL = "https://api-ssl.bitly.com/v4/shorten"
URL_PATTERN = re.compile(r"\bhttps?://[^\s<>\"'`“”‘’]+", re.IGNORECASE)
CLOSING_BRACKETS = {')': '(', ']': '[', '}': '{'}


def without_sentence_punctuation(value: str) -> str:
    while value:
        last = value[-1]
        if last in '.,!?;:…':
            value = value[:-1]
        elif last in CLOSING_BRACKETS and value.count(last) > value.count(CLOSING_BRACKETS[last]):
            value = value[:-1]
        else:
            break
    return value


def http_hostname(value: str) -> str:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError("Invalid HTTP URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid HTTP URL")
    parsed.port
    return parsed.hostname.lower()


class BitlyClient:
    def __init__(self, token: str, group: str, domain: str, endpoint: str, report, opener=None):
        self.token = token
        self.group = group
        self.domain = domain
        self.endpoint = endpoint
        self.report = report
        self.opener = opener or urllib.request.urlopen
        self.cache = {}
        self.configuration_warning_reported = False

    @classmethod
    def from_environment(cls, report):
        return cls(
            env_value('BITLY_ACCESS_TOKEN'), env_value('BITLY_GROUP_GUID'),
            env_value('BITLY_DOMAIN'), env_value('BITLY_SHORTEN_URL') or DEFAULT_SHORTEN_URL,
            report,
        )

    def request_short_link(self, url: str) -> str:
        payload = {'long_url': url, 'group_guid': self.group}
        if self.domain:
            payload['domain'] = self.domain
        request = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode('utf-8'), method='POST',
            headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'},
        )
        with self.opener(request, timeout=10) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid Bitly response")
        link = result.get('link')
        http_hostname(link)
        return link

    def shorten_url(self, url: str) -> str:
        if url in self.cache:
            return self.cache[url]
        self.cache[url] = url
        try:
            host = http_hostname(url)
            if host in {'bit.ly', self.domain.lower()}:
                return url
            if not self.token or not self.group:
                self.warn_missing_configuration()
                return url
            self.cache[url] = self.request_short_link(url)
        except (OSError, ValueError, TypeError, http.client.HTTPException) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            self.report(f'[sms-links] WARNING: Bitly unavailable ({type(error).__name__}); kept original URL')
        return self.cache[url]

    def warn_missing_configuration(self):
        if not self.configuration_warning_reported:
            self.report('[sms-links] WARNING: BITLY_ACCESS_TOKEN or BITLY_GROUP_GUID missing; kept original URLs')
            self.configuration_warning_reported = True

    def replace_url(self, match) -> str:
        original = match.group()
        url = without_sentence_punctuation(original)
        return self.shorten_url(url) + original[len(url):]

    def shorten_messages(self, messages: list[dict]) -> list[dict]:
        return [
            {**message, 'text': URL_PATTERN.sub(self.replace_url, message['text'])}
            for message in messages
        ]


def shorten_sms_messages(messages: list[dict], report) -> list[dict]:
    return BitlyClient.from_environment(report).shorten_messages(messages)
