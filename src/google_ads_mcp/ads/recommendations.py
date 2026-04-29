# pyright: basic
"""Apply Google Ads recommendations.

Separate from `ads/mutate.py` because RecommendationService is its own
endpoint, not a oneof on MutateOperation. There's no `validate_only`
preview — Google has already validated the recommendation when surfacing
it, so applying is a one-shot.
"""

from __future__ import annotations

from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors


def apply_recommendation(
    client: GoogleAdsClient,
    customer_id: str,
    recommendation_resource_name: str,
) -> list[str]:
    """Apply one recommendation; return resource_names from the response.

    The response's `resource_name` echoes the *recommendation's* identifier,
    not the entity that was created or modified — Google's API doesn't
    surface the latter directly here. For the audit trail, the recommendation
    name is enough; the entity change shows up in Google Ads change history.
    """
    service = client.get_service("RecommendationService")
    op: Any = client.get_type("ApplyRecommendationOperation")
    op.resource_name = recommendation_resource_name

    label = f"ApplyRecommendation[customer={customer_id}]"
    with translate_errors(label):
        response = service.apply_recommendation(
            customer_id=customer_id,
            operations=[op],
        )

    return [r.resource_name for r in response.results]
