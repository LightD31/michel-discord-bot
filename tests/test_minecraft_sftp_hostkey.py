"""The Minecraft SFTP connection only trusts the configured host key."""

import asyncssh
import pytest

from features.minecraft import trusted_host_keys


@pytest.fixture(scope="module")
def public_key() -> str:
    key = asyncssh.generate_private_key("ssh-ed25519")
    return key.export_public_key().decode().strip()


def test_a_bare_public_key_is_trusted(public_key):
    host_keys, ca_keys, revoked = trusted_host_keys(public_key)
    assert [k.export_public_key().decode().split()[:2] for k in host_keys] == [
        public_key.split()[:2]
    ]
    assert ca_keys == [] and revoked == []


def test_a_keyscan_line_with_the_host_in_front_is_accepted(public_key):
    (key,), _, _ = trusted_host_keys(f"[mc.example]:2225 {public_key}")
    assert key.export_public_key().decode().split()[1] == public_key.split()[1]


@pytest.mark.parametrize("bad", ["", "   ", "ssh-ed25519", "ssh-ed25519 not-base64!"])
def test_a_missing_or_broken_key_refuses_to_connect(bad):
    with pytest.raises(ValueError):
        trusted_host_keys(bad)
