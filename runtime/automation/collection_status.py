"""Safe channel health: request execution is not market-data readiness."""
import json
import socket
import ssl
from urllib.error import HTTPError, URLError


def classify_failure(exc):
    """Persist only controlled categories/numbers, never provider text or URLs."""
    if isinstance(exc, HTTPError):
        code = exc.code
        category = ("AUTHENTICATION" if code == 401 else "ACCESS_DENIED" if code == 403
                    else "RATE_LIMIT" if code == 429 else "HTTP_SERVER" if 500 <= code < 600
                    else "HTTP_ERROR")
        return {"category": category, "http_status": code,
                "retryable": code == 408 or 500 <= code < 600}
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, socket.gaierror):
        category, retryable = "DNS", True
    elif isinstance(reason, ssl.SSLError):
        result = {"category": "TLS", "http_status": None, "retryable": False}
        code = getattr(reason, 'verify_code', None)
        if type(code) is int:
            result['tls_verify_code'] = code
        known = {'CERTIFICATE_VERIFY_FAILED', 'UNEXPECTED_EOF_WHILE_READING',
                 'TLSV1_ALERT_INTERNAL_ERROR', 'SSLV3_ALERT_HANDSHAKE_FAILURE',
                 'WRONG_VERSION_NUMBER', 'TLSV1_ALERT_PROTOCOL_VERSION'}
        value = getattr(reason, 'reason', None)
        result['tls_reason'] = value if value in known else 'UNCLASSIFIED_TLS'
        return result
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        category, retryable = "TIMEOUT", True
    elif isinstance(reason, ConnectionError):
        category, retryable = "CONNECTION", True
    elif isinstance(reason, (json.JSONDecodeError, UnicodeError)):
        category, retryable = "INVALID_RESPONSE_ENCODING", False
    elif isinstance(reason, ValueError):
        category, retryable = "INVALID_RESPONSE", False
    elif isinstance(exc, URLError):
        category, retryable = "NETWORK_UNCLASSIFIED", False
    else:
        category, retryable = "UNEXPECTED_ERROR", False
    return {"category": category, "http_status": None, "retryable": retryable}


def summarize(channels, *, market_saved, new_rows, quarantined):
    """This collector stores untimed market raw data; it cannot certify quotes."""
    result = {}
    for name, counts in channels.items():
        result[name] = dict(counts, status=("RESPONSE_RECEIVED" if counts["successful_requests"]
                                           else "FAILED" if counts["requests"] else "NOT_ATTEMPTED"))
    result["history"].update(new_result_observations=new_rows, quarantined=quarantined,
                              training_eligibility="SEPARATE_RAW_EVIDENCE_AND_VENUE_AUDIT_REQUIRED")
    market = result["market"]
    market.update(raw_snapshot_saved=bool(market_saved), verified_quote_count=0,
                  model_ready=False,
                  quote_status="RAW_WITHOUT_VERIFIED_QUOTE_TIME" if market_saved
                  else "NO_RESPONSE" if market["requests"] else "NOT_ATTEMPTED_BUDGET")
    successes = sum(c["successful_requests"] for c in channels.values())
    return {"channels": result,
            "data_status": "PARTIAL_NO_VERIFIED_MARKET" if successes else "NO_USABLE_RESPONSE",
            "market_model_ready": False, "coverage_complete": False}
