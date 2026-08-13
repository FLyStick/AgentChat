import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_query_list(content, user_input):
    """Parse an LLM query-rewrite response into a list of query strings."""
    if isinstance(content, list):
        return content
    if not isinstance(content, str):
        return [user_input]

    cleaned = content.replace("```json", "").replace("```", "").strip()
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except Exception as err:
        logger.info("Query rewrite JSON parse failed: %s", err)

    array_match = re.search(r"\[[\s\S]*\]", cleaned)
    if array_match:
        try:
            result = json.loads(array_match.group())
            if isinstance(result, list):
                return result
        except Exception as err:
            logger.info("Query rewrite array extraction failed: %s", err)

    return [user_input]
