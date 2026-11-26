"""Read-only ``/v1`` gateway diagnostics for the operator console.

The backend is the only process that holds the gateway credential. This module
performs GET reads only; it has no lease, motion or stop path.
"""

from __future__ import annotations

from typing import Any

from ..adapters.gateway_v1 import (
    EXPECTED_CONTRACT_HASH,
    GatewayV1Client,
    GatewayV1Probe,
)
from ..adapters.gateway_v1_stream import GatewayV1Stream, GatewayV1StreamProbe


class GatewayV1Operations:
    def gateway_v1_status(self) -> dict[str, Any]:
        url = self.settings.gateway_v1_url
        if not url:
            return GatewayV1Probe.not_configured().to_public_dict()
        client = GatewayV1Client(
            url,
            token=self.settings.gateway_v1_token,
            timeout_s=self.settings.gateway_v1_timeout_s,
            expected_contract_hash=(
                self.settings.gateway_v1_contract_hash or EXPECTED_CONTRACT_HASH
            ),
        )
        return client.probe().to_public_dict()

    def gateway_v1_stream_probe(self) -> dict[str, Any]:
        """Read a short /v1/ws/state subscription; never sends an intent."""
        url = self.settings.gateway_v1_url
        if not url:
            return GatewayV1StreamProbe.not_configured().to_public_dict()
        stream = GatewayV1Stream(
            url,
            token=self.settings.gateway_v1_token,
            timeout_s=self.settings.gateway_v1_timeout_s,
            expected_contract_hash=(
                self.settings.gateway_v1_contract_hash or EXPECTED_CONTRACT_HASH
            ),
        )
        return stream.probe(count=3).to_public_dict()
