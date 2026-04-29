from __future__ import annotations

from worker.retrieval.codecompass_query_parser import parse_codecompass_query


def test_codecompass_query_parser_extracts_exact_symbol_and_config_terms():
    parsed = parse_codecompass_query(
        'Fix com.acme.payment.PaymentService.retryTimeout in <bean id="paymentService"> with spring.datasource.url and NullPointerException'
    )
    assert "com.acme.payment.PaymentService" in parsed["exact_symbol_terms"]
    assert "PaymentService" in parsed["exact_symbol_terms"]
    assert "retryTimeout" in parsed["exact_symbol_terms"]
    assert "bean" in parsed["exact_symbol_terms"]
    assert "spring.datasource.url" in parsed["exact_symbol_terms"]
    assert "NullPointerException" in parsed["exact_symbol_terms"]
    assert "spring.datasource.url" in parsed["broad_terms"]

